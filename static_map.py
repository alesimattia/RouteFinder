"""
Base cartografica dalla Google Maps Static API, senza tracciato nè marker: quelli
li disegna il renderer, così restano in colore pieno della palette e-paper.
"""

from __future__ import annotations

import io
import logging
import math
import time
from typing import Optional

import requests
from PIL import Image

from map_fit import Viewport


logger = logging.getLogger("routefinder.map")

ENDPOINT = "https://maps.googleapis.com/maps/api/staticmap"

# La Static API serve al massimo 640 px per lato del parametro size, sia a
# scale=1 sia a scale=2, che raddoppia i pixel a parità di copertura.
MAX_SIZE = 640
MAX_SCALE = 2
REQUEST_TIMEOUT_S = 15

# Stile pensato per il pannello a tre colori: fondo bianco, strade in grigi
# che il dither rende come trame, nessun simbolo, e solo i nomi delle località,
# che orientano senza affollare. Il rosso resta libero per il tracciato.
STYLE = (
    "saturation:-100",
    "element:labels|visibility:off",
    "feature:administrative.locality|element:labels.text|visibility:on",
    "feature:administrative.locality|element:labels.text.fill|color:0x000000",
    "feature:administrative.locality|element:labels.text.stroke|color:0xffffff|weight:4",
    "feature:poi|visibility:off",
    "feature:transit|visibility:off",
    "feature:administrative|element:geometry|visibility:off",
    "feature:landscape|element:geometry|color:0xffffff",
    "feature:water|element:geometry|color:0xd8d8d8",
    "feature:road|element:geometry.stroke|visibility:off",
    "feature:road.highway|element:geometry.fill|color:0x505050",
    "feature:road.arterial|element:geometry.fill|color:0x909090",
    "feature:road.local|element:geometry.fill|color:0xc8c8c8",
)


class StaticMapError(RuntimeError):
    """La Static API non ha restituito un'immagine utilizzabile."""


def scale_for(width: int, height: int) -> int:
    """Scale della Static API necessario per la misura; ValueError oltre i limiti."""
    if width < 1 or height < 1:
        raise ValueError("misura non positiva")
    if width <= MAX_SIZE and height <= MAX_SIZE:
        return 1
    if width <= MAX_SIZE * MAX_SCALE and height <= MAX_SIZE * MAX_SCALE:
        return 2
    raise ValueError(f"misura oltre {MAX_SIZE * MAX_SCALE} px per lato")


def build_params(viewport: Viewport, api_key: str) -> list[tuple[str, str]]:
    """Parametri della richiesta; lista di coppie perchè style si ripete."""
    size_w = math.ceil(viewport.width / viewport.scale)
    size_h = math.ceil(viewport.height / viewport.scale)
    params = [
        ("center", f"{viewport.center_lat:.6f},{viewport.center_lng:.6f}"),
        ("zoom", str(viewport.zoom)),
        ("size", f"{size_w}x{size_h}"),
        ("scale", str(viewport.scale)),
        ("maptype", "roadmap"),
        ("format", "png32"),
        ("language", "it"),
        ("region", "IT"),
    ]
    params.extend(("style", s) for s in STYLE)
    params.append(("key", api_key))
    return params


def fetch_base_map(viewport: Viewport, api_key: str, session: Optional[requests.Session] = None) -> Image.Image:
    """
    Scarica la base a centro e zoom del viewport e la porta alla misura esatta.

    Con scale=2 e una misura dispari la Static API restituisce un pixel in più:
    si toglie a destra e in basso, e il logo in basso a sinistra resta intero.
    """
    http = session or requests
    started = time.monotonic()
    try:
        response = http.get(ENDPOINT, params=build_params(viewport, api_key), timeout=REQUEST_TIMEOUT_S)
    except requests.RequestException as exc:
        raise StaticMapError(f"Static API non raggiungibile: {exc}") from exc
    elapsed_ms = (time.monotonic() - started) * 1000
    if response.status_code != 200 or not response.headers.get("Content-Type", "").startswith("image/"):
        logger.warning("Static API %d in %.0f ms: %s", response.status_code, elapsed_ms, response.text[:300])
        raise StaticMapError(f"Static API ha risposto {response.status_code}")
    image = Image.open(io.BytesIO(response.content)).convert("RGB")
    logger.info("Static API ok in %.0f ms: %dx%d zoom %d", elapsed_ms, image.width, image.height, viewport.zoom)
    if image.width < viewport.width or image.height < viewport.height:
        raise StaticMapError(f"immagine {image.width}x{image.height} più piccola del richiesto")
    return image.crop((0, 0, viewport.width, viewport.height))
