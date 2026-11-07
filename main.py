"""
FastAPI app di routefinder: percorso più veloce col traffico fra due punti,
servito come JPEG a colori pieni o come piani 1bpp per il pannello e-paper.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Literal, Optional

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

import auth
from cache import TTLCache
from examples import DEFAULT_CANVAS, MAX_CANVAS, VIEWPORT_PAGE, render_examples
from examples import DESTINATION as EXAMPLE_DESTINATION, ORIGIN as EXAMPLE_ORIGIN
from renderer import render_route_image
from routes_api import RouteResult, RoutesError, compute_routes
from static_map import MAX_SIDE, StaticMapError, fetch_base_map


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("routefinder")

API_KEY_ENV = "GOOGLE_MAPS_API_KEY"

# Il traffico cambia in minuti: oltre questo tempo un percorso in cache sarebbe
# un'informazione vecchia. Basta a non pagare due volte un refresh del browser.
ROUTE_CACHE_TTL_S = 300

MIN_SIDE = 64

# Palette del pannello quando la richiesta non la indica: default di
# format=arduino e palette aggiunta all'URL di un JPEG con dither senza colors.
DEFAULT_COLORS = "bwr"

app = FastAPI(
    title="Routefinder",
    description="Percorso più veloce col traffico per il pannello e-paper ESP32",
    version="1.0.0",
)


class _ConditionalGZipMiddleware:
    """GZip per le pagine testuali, mai sulle immagini: il firmware confronta il
    Content-Length di /route con la misura attesa, e JPEG e PNG sono già compressi."""

    _SKIP_PATHS = frozenset({"/route", "/example"})

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


def _endpoints(origin: Optional[str], destination: Optional[str]) -> tuple[tuple[float, float], tuple[float, float]]:
    """Partenza e arrivo validati, comuni a /route e /example; 400 se mancano."""
    if origin is None or destination is None:
        raise HTTPException(status_code=400, detail="origin e destination sono obbligatori")
    return parse_point(origin, "origin"), parse_point(destination, "destination")


def _check_size(width: int, height: int) -> None:
    """400 fuori dai limiti, mai un clamp silenzioso: il firmware confronta il Content-Length."""
    if not (MIN_SIDE <= width <= MAX_SIDE and MIN_SIDE <= height <= MAX_SIDE):
        raise HTTPException(status_code=400, detail=f"misura fra {MIN_SIDE} e {MAX_SIDE} px per lato")


def _cached_route(origin_point: tuple[float, float], destination_point: tuple[float, float],
                  fuel: str) -> tuple[RouteResult, Optional[float]]:
    """Percorso dalla cache di ROUTE_CACHE_TTL_S o da una chiamata alla Routes
    API; il secondo valore è la durata della chiamata, None se non c'è stata.
    /example la mostra nel riepilogo."""
    api_key = _api_key()
    elapsed: list[float] = []

    def call() -> RouteResult:
        started = time.perf_counter()
        value = compute_routes(origin_point, destination_point, fuel, api_key)
        elapsed.append(time.perf_counter() - started)
        return value

    try:
        result = _route_cache.get_or_compute((origin_point, destination_point, fuel), call)
    except RoutesError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return result, (elapsed[0] if elapsed else None)


@app.get("/")
def root():
    """Rimando alla documentazione interattiva di FastAPI, con tutti i parametri."""
    return RedirectResponse(url="/docs")


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
    width: int = Query(MAX_SIDE, description=f"Larghezza in px, al massimo {MAX_SIDE}; senza, la massima."),
    height: int = Query(MAX_SIDE, description=f"Altezza in px, al massimo {MAX_SIDE}; senza, la massima."),
    format: Literal["jpg", "arduino"] = Query("jpg"),
    colors: Optional[Literal["bw", "bwr", "bwry"]] = Query(
        None, description=f"Palette del pannello. Con format=arduino default {DEFAULT_COLORS}; con format=jpg "
                          "senza colors il JPEG è a colori pieni, e un dither senza colors rimanda all'URL "
                          f"con colors={DEFAULT_COLORS}."),
    dither: Optional[Literal["no", "floyd", "atkinson", "bayer"]] = Query(
        None, description="Dither verso la palette. Con format=arduino default floyd, con ogni colors; "
                          "con format=jpg default nessuno (a soglia se c'è colors)."),
):
    """
    Percorso più veloce col traffico, con l'alternativa a minor consumo per il
    carburante se diversa, disegnato su mappa Google.

    format=jpg (default) è a colori pieni; con colors è un'anteprima nella
    palette del pannello, quantizzata come format=arduino, a soglia se manca
    dither. Un dither senza colors rimanda con 307 allo stesso URL con
    colors=DEFAULT_COLORS, così la palette scelta si vede nella barra degli
    indirizzi.
    format=arduino restituisce i piani 1bpp header-less nel formato di
    /cinema/arduino della webapp: Content-Length = piani * ceil(width/8) * height,
    nessuna compressione; default colors=DEFAULT_COLORS e dither=floyd.
    """
    if not auth.is_authorized(request):
        return _unauthorized(request)
    origin_point, destination_point = _endpoints(origin, destination)
    _check_size(width, height)

    headers = {"Cache-Control": "no-store"}
    if format == "arduino":
        color_mode, dither_mode = colors or DEFAULT_COLORS, dither or "floyd"
    elif colors is None and dither is None:
        color_mode, dither_mode = "rgb", "no"
    elif colors is None:
        # Il dither ha bisogno di una palette: invece di sceglierla in silenzio si
        # corregge l'URL, prima di ogni chiamata a pagamento. La query originale si
        # tiene così com'è, senza ricodificare le virgole di origin e destination.
        return RedirectResponse(url=f"{request.url.path}?{request.url.query}&colors={DEFAULT_COLORS}",
                                status_code=307)
    else:
        color_mode, dither_mode = colors, dither or "no"

    result, _ = _cached_route(origin_point, destination_point, fuel)
    api_key = _api_key()
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


@app.get("/example")
def example(
    request: Request,
    fuel: Literal["gasoline", "diesel", "hybrid", "electric"] = Query("gasoline"),
    width: Optional[int] = Query(
        None, description=f"Larghezza dell'immagine in px, al massimo {MAX_CANVAS[0]}. Dal browser, senza "
                          "width e height, una pagina li aggiunge all'URL presi dalla viewport; "
                          f"altrimenti default {DEFAULT_CANVAS[0]}."),
    height: Optional[int] = Query(
        None, description=f"Altezza dell'immagine in px, al massimo {MAX_CANVAS[1]}; default {DEFAULT_CANVAS[1]}."),
):
    """
    PNG grande quanto la viewport del browser con la stessa immagine del
    percorso fisso fra examples.ORIGIN e examples.DESTINATION, a colori pieni e
    in ogni palette e dither del pannello, in celle piccole quanto basta a stare
    nella tela, con tempo, RAM e peso a piena misura di ciascuna. Una chiamata
    alla Routes API (nessuna se il percorso è in cache) e una alla Static API,
    la base a colori che ogni cella converte nella sua palette.
    """
    if not auth.is_authorized(request):
        return _unauthorized(request)
    origin_point, destination_point = _endpoints(EXAMPLE_ORIGIN, EXAMPLE_DESTINATION)
    if width is None and height is None and auth.wants_html(request):
        # Il server non conosce la viewport: la pagina la legge e torna qui con
        # width e height, prima di ogni chiamata a pagamento.
        return HTMLResponse(VIEWPORT_PAGE, headers={"Cache-Control": "no-store"})
    canvas_w = width if width is not None else DEFAULT_CANVAS[0]
    canvas_h = height if height is not None else DEFAULT_CANVAS[1]
    if not (0 < canvas_w <= MAX_CANVAS[0] and 0 < canvas_h <= MAX_CANVAS[1]):
        raise HTTPException(status_code=400, detail=f"immagine al massimo {MAX_CANVAS[0]}x{MAX_CANVAS[1]} px")

    result, routes_s = _cached_route(origin_point, destination_point, fuel)
    api_key = _api_key()
    route_query = (f"/route?origin={origin_point[0]},{origin_point[1]}"
                   f"&destination={destination_point[0]},{destination_point[1]}&fuel={fuel}")
    image_key = ("example", origin_point, destination_point, fuel, canvas_w, canvas_h,
                 result.computed_at.isoformat())
    try:
        body = _image_cache.get_or_compute(image_key, lambda: render_examples(
            result, canvas_w, canvas_h, route_query, routes_s,
            lambda viewport: fetch_base_map(viewport, api_key)))
    except StaticMapError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return Response(content=body, media_type="image/png", headers={"Cache-Control": "no-store"})
