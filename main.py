"""
FastAPI app di routefinder: percorso più veloce col traffico fra due punti,
servito come JPEG a colori pieni o come piani 1bpp per il pannello e-paper.
"""

from __future__ import annotations

import logging
import os
from typing import Literal, Optional

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

import auth
from cache import TTLCache
from renderer import render_route_image
from routes_api import RoutesError, compute_routes
from static_map import StaticMapError, fetch_base_map


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("routefinder")

API_KEY_ENV = "GOOGLE_MAPS_API_KEY"

# Il traffico cambia in minuti: oltre questo tempo un percorso in cache sarebbe
# un'informazione vecchia. Basta a non pagare due volte un refresh del browser.
ROUTE_CACHE_TTL_S = 300

MIN_SIDE = 64

app = FastAPI(
    title="Routefinder",
    description="Percorso più veloce col traffico per il pannello e-paper ESP32",
    version="1.0.0",
)


class _ConditionalGZipMiddleware:
    """GZip per le pagine testuali, mai su /route: il firmware confronta il
    Content-Length con la misura attesa, e il JPEG è già compresso."""

    _SKIP_PATHS = frozenset({"/route"})

    def __init__(self, app: ASGIApp, **kwargs):
        self._gzip = GZipMiddleware(app, **kwargs)
        self._raw = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope.get("type") == "http" and scope.get("path") in self._SKIP_PATHS:
            await self._raw(scope, receive, send)
        else:
            await self._gzip(scope, receive, send)


app.add_middleware(_ConditionalGZipMiddleware, minimum_size=500)

_route_cache = TTLCache(ttl_seconds=ROUTE_CACHE_TTL_S)
_image_cache = TTLCache(ttl_seconds=ROUTE_CACHE_TTL_S)


def parse_point(value: str, name: str) -> tuple[float, float]:
    """"lat,lng" in gradi decimali, nel range geografico; 400 altrimenti."""
    parts = value.split(",")
    try:
        if len(parts) != 2:
            raise ValueError
        lat, lng = float(parts[0]), float(parts[1])
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{name} non valido (atteso lat,lng): {value!r}")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise HTTPException(status_code=400, detail=f"{name} fuori range: {value!r}")
    return round(lat, 6), round(lng, 6)


def _api_key() -> str:
    key = os.environ.get(API_KEY_ENV, "")
    if not key:
        raise HTTPException(status_code=503, detail=f"{API_KEY_ENV} non configurata sul servizio")
    return key


def _unauthorized(request: Request) -> Response:
    """Pagina di accesso per il browser, 401 secco per tutti gli altri."""
    if auth.wants_html(request):
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return HTMLResponse(auth.login_page(auth.safe_next(target)), status_code=401)
    return Response(status_code=401, headers={"WWW-Authenticate": f'Token header="{auth.HEADER_NAME}"'})


@app.get("/")
def root():
    """Rimando all'endpoint principale."""
    return RedirectResponse(url="/route")


@app.get("/health")
def health():
    """Liveness probe di render.yaml e bersaglio del ping di pre-warm del
    firmware: aperto, perchè non tocca le API a pagamento."""
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/auth", response_class=HTMLResponse)
def auth_form(next: Optional[str] = None):
    """Pagina in cui incollare il token."""
    return HTMLResponse(auth.login_page(auth.safe_next(next)))


@app.post("/auth")
def auth_submit(token: str = Form(...), next: Optional[str] = Form(None)):
    """Verifica il token incollato e lo salva in un cookie, poi torna alla pagina richiesta."""
    target = auth.safe_next(next)
    if not auth.token_matches(token.strip()):
        return HTMLResponse(auth.login_page(target, "Token non valido"), status_code=401)
    response = RedirectResponse(url=target, status_code=303)
    response.set_cookie(auth.COOKIE_NAME, token.strip(), max_age=auth.COOKIE_MAX_AGE_S,
                        httponly=True, secure=True, samesite="strict")
    return response


@app.get("/route")
def route(
    request: Request,
    origin: Optional[str] = Query(None, description="Partenza, lat,lng in gradi decimali."),
    destination: Optional[str] = Query(None, description="Arrivo, lat,lng in gradi decimali."),
    fuel: Literal["gasoline", "diesel", "hybrid", "electric"] = Query("gasoline"),
    width: int = Query(610, description="Larghezza in px, al massimo 1280."),
    height: int = Query(290, description="Altezza in px, al massimo 1280."),
    format: Literal["jpg", "arduino"] = Query("jpg"),
    colors: Optional[Literal["bw", "bwr", "bwry"]] = Query(None, description="Solo con format=arduino."),
    dither: Optional[Literal["no", "floyd", "atkinson", "bayer"]] = Query(None, description="Solo con format=arduino."),
):
    """
    Percorso più veloce col traffico, con l'alternativa a minor consumo per il
    carburante se diversa, disegnato su mappa Google.

    format=jpg (default) è a colori pieni e ignora colors e dither. format=arduino
    restituisce i piani 1bpp header-less nel formato di /cinema/arduino della
    webapp: Content-Length = piani * ceil(width/8) * height, nessuna compressione.
    """
    if not auth.is_authorized(request):
        return _unauthorized(request)
    if origin is None or destination is None:
        raise HTTPException(status_code=400, detail="origin e destination sono obbligatori")
    origin_point = parse_point(origin, "origin")
    destination_point = parse_point(destination, "destination")
    if not (MIN_SIDE <= width <= 1280 and MIN_SIDE <= height <= 1280):
        raise HTTPException(status_code=400, detail=f"misura fra {MIN_SIDE} e 1280 px per lato")

    headers = {"Cache-Control": "no-store"}
    if format == "jpg":
        if colors is not None or dither is not None:
            headers["X-Routefinder-Warning"] = "colors e dither ignorati con format=jpg"
        color_mode, dither_mode = "rgb", "no"
    else:
        color_mode, dither_mode = colors or "bwr", dither or "floyd"

    api_key = _api_key()
    try:
        result = _route_cache.get_or_compute(
            (origin_point, destination_point, fuel),
            lambda: compute_routes(origin_point, destination_point, fuel, api_key))
    except RoutesError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    image_key = (origin_point, destination_point, fuel, width, height, format, color_mode,
                 dither_mode, result.computed_at.isoformat())
    try:
        body = _image_cache.get_or_compute(image_key, lambda: render_route_image(
            result, width, height, format, color_mode, dither_mode,
            lambda viewport: fetch_base_map(viewport, api_key)))
    except StaticMapError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    media_type = "image/jpeg" if format == "jpg" else "application/octet-stream"
    return Response(content=body, media_type=media_type, headers=headers)
