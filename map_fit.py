"""
Proiezione Web Mercator della Maps Static API e scelta di zoom e centro della
mappa, in modo che il tracciato stia tutto nell'immagine e fuori dalle zone che
non può coprire: il riquadro informativo e logo e attribuzione di Google.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np


TILE_SIZE = 256
MAX_ZOOM = 18
MIN_ZOOM = 1

# Latitudine oltre la quale Web Mercator diverge; Google taglia la mappa a ~85°.
_SIN_LAT_LIMIT = 0.9999


def world_xy(lat: float, lng: float) -> tuple[float, float]:
    """Coordinate mondo a zoom 0, in [0, 256), con l'origine nell'angolo nord-ovest."""
    sin_lat = min(max(math.sin(math.radians(lat)), -_SIN_LAT_LIMIT), _SIN_LAT_LIMIT)
    x = TILE_SIZE * (0.5 + lng / 360.0)
    y = TILE_SIZE * (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi))
    return x, y


def world_to_latlng(x: float, y: float) -> tuple[float, float]:
    """Inversa di world_xy()."""
    lng = (x / TILE_SIZE - 0.5) * 360.0
    n = math.pi * (1 - 2 * y / TILE_SIZE)
    lat = math.degrees(math.atan(math.sinh(n)))
    return lat, lng


@dataclass(frozen=True)
class Rect:
    """Rettangolo in pixel dell'immagine, estremi inclusi."""

    x0: int
    y0: int
    x1: int
    y1: int

    def expanded(self, amount: int) -> "Rect":
        """Lo stesso rettangolo allargato di `amount` pixel su ogni lato."""
        return Rect(self.x0 - amount, self.y0 - amount, self.x1 + amount, self.y1 + amount)


@dataclass(frozen=True)
class Marker:
    """Ingombro di un simbolo ancorato a un punto: offset in pixel dal punto, estremi inclusi."""

    lat: float
    lng: float
    dx0: int
    dy0: int
    dx1: int
    dy1: int


@dataclass(frozen=True)
class Viewport:
    """
    Centro e zoom della mappa, nella forma che si passa alla Static API, e la
    proiezione che porta un punto geografico sul pixel dell'immagine restituita.
    `scale` è il parametro omonimo della Static API: a 2 i pixel raddoppiano e
    la copertura resta la stessa.
    """

    center_lat: float
    center_lng: float
    zoom: int
    width: int
    height: int
    scale: int

    @property
    def pixels_per_world_unit(self) -> float:
        return float(2 ** self.zoom * self.scale)

    def project(self, lat: float, lng: float) -> tuple[float, float]:
        """Pixel dell'immagine, anche frazionario o fuori dai bordi."""
        k = self.pixels_per_world_unit
        cx, cy = world_xy(self.center_lat, self.center_lng)
        x, y = world_xy(lat, lng)
        return (x - cx) * k + self.width / 2, (y - cy) * k + self.height / 2


def _densify(pixels: np.ndarray) -> np.ndarray:
    """Punti del tracciato a passo di al più un pixel, così il controllo per
    punti vede anche il segmento che attraversa una zona vietata fra due vertici."""
    if len(pixels) < 2:
        return pixels
    chunks = [pixels[:1]]
    for start, end in zip(pixels[:-1], pixels[1:]):
        steps = int(math.ceil(float(np.max(np.abs(end - start)))))
        if steps <= 0:
            continue
        t = np.arange(1, steps + 1, dtype=np.float64)[:, None] / steps
        chunks.append(start + (end - start) * t)
    return np.concatenate(chunks)


def _rect_counts(sat: np.ndarray, x0: np.ndarray, y0: np.ndarray, x1: np.ndarray, y1: np.ndarray) -> np.ndarray:
    """Somma della bitmap sui rettangoli dati, vettoriale, con estremi inclusi già
    tagliati alla bitmap; un rettangolo vuoto (x0 > x1) somma zero."""
    empty = (x0 > x1) | (y0 > y1)
    x0c, y0c = np.where(empty, 0, x0), np.where(empty, 0, y0)
    x1c, y1c = np.where(empty, -1, x1), np.where(empty, -1, y1)
    total = sat[y1c + 1, x1c + 1] - sat[y0c, x1c + 1] - sat[y1c + 1, x0c] + sat[y0c, x0c]
    return np.where(empty, 0, total)


def fit_viewport(
    paths: Sequence[Sequence[tuple[float, float]]],
    markers: Iterable[Marker],
    width: int,
    height: int,
    forbidden: Sequence[Rect],
    margin: int,
    padding: int,
    scale: int = 1,
    max_zoom: int = MAX_ZOOM,
    min_zoom: int = MIN_ZOOM,
) -> Viewport:
    """
    Sceglie lo zoom più alto e, a quello zoom, il centro per cui tracciati e
    simboli stanno dentro l'immagine meno `margin` e fuori da ogni rettangolo
    di `forbidden`, con `padding` di distanza per lo spessore della linea.

    A ogni zoom il tracciato si rasterizza in una bitmap e una summed-area table
    dà in O(1) quanti pixel una traslazione fa cadere in una zona vietata; fra le
    traslazioni ammissibili vince quella che porta il tracciato più vicino al
    centro dell'immagine. Scendendo di zoom il tracciato si riduce verso un
    punto, quindi un percorso più corto dell'area libera trova sempre posto.
    """
    markers = list(markers)
    geo_points = [p for path in paths for p in path] + [(m.lat, m.lng) for m in markers]
    if not geo_points:
        raise ValueError("nessun punto da inquadrare")
    world_paths = [np.array([world_xy(lat, lng) for lat, lng in path], dtype=np.float64)
                   for path in paths if len(path) > 0]
    world_markers = [(world_xy(m.lat, m.lng), m) for m in markers]
    inset = margin + padding
    if width - 2 * inset < 1 or height - 2 * inset < 1:
        raise ValueError("immagine troppo piccola per il margine richiesto")

    for zoom in range(max_zoom, min_zoom - 1, -1):
        k = float(2 ** zoom * scale)
        # L'ingombro dei soli vertici scarta in fretta gli zoom troppo alti, prima
        # di densificare tracciati che a zoom 18 sono decine di migliaia di pixel.
        vertex_span_x = max(float(np.ptp(wp[:, 0])) for wp in world_paths) * k if world_paths else 0.0
        vertex_span_y = max(float(np.ptp(wp[:, 1])) for wp in world_paths) * k if world_paths else 0.0
        if vertex_span_x > width or vertex_span_y > height:
            continue
        pixel_sets = [np.rint(_densify(wp * k)).astype(np.int64) for wp in world_paths]
        marker_rects = []
        for (wx, wy), m in world_markers:
            px, py = int(round(wx * k)), int(round(wy * k))
            marker_rects.append((px + m.dx0, py + m.dy0, px + m.dx1, py + m.dy1))

        xs = [p[:, 0] for p in pixel_sets] + [np.array([r[0], r[2]]) for r in marker_rects]
        ys = [p[:, 1] for p in pixel_sets] + [np.array([r[1], r[3]]) for r in marker_rects]
        min_x, max_x = int(min(a.min() for a in xs)), int(max(a.max() for a in xs))
        min_y, max_y = int(min(a.min() for a in ys)), int(max(a.max() for a in ys))
        box_w, box_h = max_x - min_x + 1, max_y - min_y + 1
        if box_w > width - 2 * inset or box_h > height - 2 * inset:
            continue

        bitmap = np.zeros((box_h, box_w), dtype=np.int32)
        for p in pixel_sets:
            bitmap[p[:, 1] - min_y, p[:, 0] - min_x] = 1
        for x0, y0, x1, y1 in marker_rects:
            bitmap[y0 - min_y:y1 - min_y + 1, x0 - min_x:x1 - min_x + 1] = 1
        sat = np.zeros((box_h + 1, box_w + 1), dtype=np.int64)
        sat[1:, 1:] = bitmap.cumsum(axis=0).cumsum(axis=1)

        # (ox, oy) = pixel dell'immagine su cui cade l'angolo della bitmap.
        ox_values = np.arange(inset, width - inset - box_w + 1)
        oy_values = np.arange(inset, height - inset - box_h + 1)
        ox, oy = np.meshgrid(ox_values, oy_values)
        feasible = np.ones(ox.shape, dtype=bool)
        for rect in forbidden:
            r = rect.expanded(padding)
            counts = _rect_counts(
                sat,
                np.clip(r.x0 - ox, 0, box_w - 1), np.clip(r.y0 - oy, 0, box_h - 1),
                np.where(r.x1 - ox >= 0, np.clip(r.x1 - ox, 0, box_w - 1), -1),
                np.where(r.y1 - oy >= 0, np.clip(r.y1 - oy, 0, box_h - 1), -1),
            )
            # Un rettangolo tutto a sinistra o sopra la bitmap va escluso del tutto:
            # il clip lo avrebbe schiacciato sulla prima colonna o riga.
            outside = (r.x0 - ox > box_w - 1) | (r.y0 - oy > box_h - 1)
            feasible &= (counts == 0) | outside
        if not feasible.any():
            continue

        distance = (ox + box_w / 2 - width / 2) ** 2 + (oy + box_h / 2 - height / 2) ** 2
        distance = np.where(feasible, distance, np.inf)
        row, col = np.unravel_index(int(np.argmin(distance)), distance.shape)
        best_ox, best_oy = int(ox[row, col]), int(oy[row, col])

        center_wx = (width / 2 - best_ox + min_x) / k
        center_wy = (height / 2 - best_oy + min_y) / k
        lat, lng = world_to_latlng(center_wx, center_wy)
        # Sei decimali sono la precisione che va nell'URL: la proiezione usata per
        # disegnare deve partire dallo stesso centro della mappa scaricata.
        return Viewport(round(lat, 6), round(lng, 6), zoom, width, height, scale)

    raise ValueError("tracciato non inquadrabile nemmeno allo zoom minimo")
