"""
Composizione dell'immagine del percorso: base cartografica, tracciati, marker e
riquadro informativo in alto a destra.

Lo stesso codice di disegno serve il JPEG a colori pieni e i piani e-paper:
nel secondo caso la base si quantizza prima, e tutto il resto si disegna dopo
sugli indici di palette, quindi testo, icone e tracciati restano in colore
pieno senza errore di dither attorno.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
from PIL import Image, ImageDraw

import gfx_font
import material_icons
from arduino_format import encode as encode_arduino
from dithering import PALETTES, indices_to_p_image, quantize
from map_fit import Marker, Rect, Viewport, fit_viewport
from routes_api import RouteInfo, RouteResult
from static_map import scale_for


# Misure a scale=1: con scale=2 tutto si raddoppia, compreso il riquadro.
MARGIN = 6
LINE_PADDING = 6
FASTEST_WIDTH = 4
ECO_WIDTH = 3
CASING = 2
ECO_DASH = 9
ECO_GAP = 6
ORIGIN_SIDE = 18
DESTINATION_SIDE = 26
ICON_HALO = 2

CARD_PADDING = 10
CARD_ROW_GAP = 7
CARD_ICON_GAP = 8
CARD_RADIUS = 10
CARD_BORDER = 2

# Oltre questo ritardo la riga del traffico passa al rosso.
DELAY_ALERT_S = 300

# Logo Google in basso a sinistra e attribuzione in basso a destra: misure
# abbondanti della resa a scale=1, che il tracciato non deve coprire.
GOOGLE_LOGO = (72, 28)
GOOGLE_ATTRIBUTION = (210, 16)

FONT_MAIN = "FreeSansBold18pt7b"
FONT_BODY = "FreeSans12pt7b"
FONT_SMALL = "FreeSans9pt7b"


@dataclass(frozen=True)
class CardRow:
    """Una riga del riquadro: icona Material, testo ASCII, font GFX e colore."""

    icon: str
    text: str
    font: str
    color: str


def format_duration(seconds: int) -> str:
    """Durata in minuti, arrotondata; da un'ora in su "1 h 05 min"."""
    minutes = max(0, int(round(seconds / 60)))
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


def format_distance(meters: int) -> str:
    """Distanza con la virgola decimale; da 100 km senza decimali."""
    km = meters / 1000
    if km >= 100:
        return f"{int(round(km))} km"
    return f"{km:.1f}".replace(".", ",") + " km"


def format_liters(microliters: int) -> str:
    """Litri con un decimale e la virgola."""
    return f"{microliters / 1e6:.1f}".replace(".", ",") + " L"


def card_rows(result: RouteResult) -> list[CardRow]:
    """Righe del riquadro nell'ordine di lettura: tempo stimato, ritardo,
    distanza, alternativa eco se esiste, ora del calcolo."""
    fastest = result.fastest
    rows = [CardRow("schedule", format_duration(fastest.duration_s), FONT_MAIN, "black")]
    delay = fastest.delay_s
    if delay is not None:
        rows.append(CardRow("traffic", "+" + format_duration(delay), FONT_BODY,
                            "red" if delay >= DELAY_ALERT_S else "black"))
    rows.append(CardRow("straighten", format_distance(fastest.distance_m), FONT_BODY, "black"))
    eco = result.eco
    if eco is not None:
        text = "+" + format_duration(eco.duration_s - fastest.duration_s)
        if eco.fuel_microliters is not None and fastest.fuel_microliters is not None:
            saved = fastest.fuel_microliters - eco.fuel_microliters
            if saved > 0:
                text += " -" + format_liters(saved)
        rows.append(CardRow("eco", text, FONT_BODY, "black"))
    rows.append(CardRow("update", "agg. " + result.computed_at.strftime("%H:%M"), FONT_SMALL, "black"))
    return rows


def _icon_side(font: gfx_font.GfxFont) -> int:
    """Lato dell'icona per una riga: un po' più alta delle maiuscole, come le
    icone accanto al testo nelle linee guida Material."""
    return int(round(font.cap_height * 1.4))


def card_size(rows: list[CardRow]) -> tuple[int, int]:
    """Larghezza e altezza del riquadro a scale=1."""
    width = 0
    height = 0
    for row in rows:
        font = gfx_font.load(row.font)
        side = _icon_side(font)
        width = max(width, side + CARD_ICON_GAP + font.text_bounds(row.text)[2])
        height += max(side, font.cap_height)
    height += CARD_ROW_GAP * (len(rows) - 1)
    return width + 2 * CARD_PADDING, height + 2 * CARD_PADDING


def draw_card(rows: list[CardRow], colors: dict[str, object], mode: str) -> Image.Image:
    """Riquadro a scale=1 come immagine a sè, nel modo dell'immagine finale:
    card bianca ad angoli arrotondati, righe icona più testo su una griglia."""
    width, height = card_size(rows)
    card = Image.new(mode, (width, height), colors["white"])
    if mode == "P":
        card.putpalette(_palette_bytes(colors))
    ImageDraw.Draw(card).rounded_rectangle((0, 0, width - 1, height - 1), radius=CARD_RADIUS,
                                           fill=colors["white"], outline=colors["black"], width=CARD_BORDER)
    y = CARD_PADDING
    for row in rows:
        font = gfx_font.load(row.font)
        side = _icon_side(font)
        row_height = max(side, font.cap_height)
        icon_top = y + (row_height - side) // 2
        card.paste(colors[row.color], (CARD_PADDING, icon_top), material_icons.mask(row.icon, side))
        baseline = y + (row_height + font.cap_height) // 2
        font.draw(card, CARD_PADDING + side + CARD_ICON_GAP, baseline, row.text, colors[row.color])
        y += row_height + CARD_ROW_GAP
    return card


def _palette_bytes(colors: dict[str, object]) -> list[int]:
    """Palette RGB lunga 768 per un'immagine "P" disegnata a parte."""
    return list(colors["_palette"])


def _color_table(fmt: str, color_mode: str) -> tuple[str, dict[str, object]]:
    """Modo dell'immagine e valore di ogni colore logico: tupla RGB per il JPEG,
    indice di palette per i piani. Senza rosso nella palette il rosso è nero."""
    if fmt == "jpg":
        return "RGB", {"white": (255, 255, 255), "black": (0, 0, 0), "red": (255, 0, 0)}
    palette = PALETTES[color_mode]
    flat = [c for rgb in palette for c in rgb]
    flat += [0] * (768 - len(flat))
    red = 2 if len(palette) > 2 else 1
    return "P", {"white": 0, "black": 1, "red": red, "_palette": flat}


def _dashed(points: list[tuple[float, float]], dash: float, gap: float) -> list[list[tuple[float, float]]]:
    """Spezza la polilinea in tratti lunghi `dash` separati da `gap`, misurati
    lungo il percorso e non per segmento, così il tratteggio resta regolare."""
    pieces: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = [points[0]] if points else []
    drawing = True
    remaining = dash
    for (x0, y0), (x1, y1) in zip(points[:-1], points[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        position = 0.0
        while length - position > remaining:
            position += remaining
            t = position / length
            split = (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)
            if drawing:
                current.append(split)
                pieces.append(current)
            current = [split]
            drawing = not drawing
            remaining = dash if drawing else gap
        remaining -= length - position
        if drawing:
            current.append((x1, y1))
    if drawing and len(current) > 1:
        pieces.append(current)
    return pieces


def _paste_icon(image: Image.Image, colors: dict[str, object], name: str, side: int,
                left: int, top: int, color: str, halo: int) -> None:
    """Icona con un bordo bianco di `halo` pixel che la stacca dalla mappa."""
    icon = material_icons.mask(name, side)
    image.paste(colors["white"], (left - halo, top - halo), material_icons.halo(icon, halo))
    image.paste(colors[color], (left, top), icon)


def _markers(route: RouteInfo, scale: int) -> list[Marker]:
    """Ingombri dei simboli di partenza (centrato) e arrivo (punta in basso)."""
    origin, destination = route.points[0], route.points[-1]
    half_o = (ORIGIN_SIDE // 2 + ICON_HALO) * scale
    half_d = (DESTINATION_SIDE // 2 + ICON_HALO) * scale
    return [
        Marker(origin[0], origin[1], -half_o, -half_o, half_o, half_o),
        Marker(destination[0], destination[1], -half_d, -(DESTINATION_SIDE + ICON_HALO) * scale,
               half_d, ICON_HALO * scale),
    ]


def render_route_image(
    result: RouteResult,
    width: int,
    height: int,
    fmt: str,
    color_mode: str,
    dither: str,
    fetch_map: Callable[[Viewport], Image.Image],
) -> bytes:
    """
    Immagine finale: JPEG a colori pieni se fmt è "jpg", altrimenti i piani
    e-paper header-less di arduino_format. ValueError se il riquadro non entra
    nella misura richiesta.
    """
    scale = scale_for(width, height)
    rows = card_rows(result)
    card_w, card_h = (v * scale for v in card_size(rows))
    margin = MARGIN * scale
    if card_w + 2 * margin > width or card_h + 2 * margin > height:
        raise ValueError(f"misura {width}x{height} troppo piccola per il riquadro {card_w}x{card_h}")
    card_left, card_top = width - margin - card_w, margin

    forbidden = [
        Rect(card_left, card_top, card_left + card_w - 1, card_top + card_h - 1).expanded(margin),
        Rect(0, height - GOOGLE_LOGO[1] * scale, GOOGLE_LOGO[0] * scale, height - 1),
        Rect(width - GOOGLE_ATTRIBUTION[0] * scale, height - GOOGLE_ATTRIBUTION[1] * scale, width - 1, height - 1),
    ]
    paths = [result.fastest.points] + ([result.eco.points] if result.eco else [])
    viewport = fit_viewport(paths, _markers(result.fastest, scale), width, height, forbidden,
                            margin=margin, padding=LINE_PADDING * scale, scale=scale)
    base = fetch_map(viewport)

    mode, colors = _color_table(fmt, color_mode)
    if mode == "RGB":
        image = base.convert("RGB")
    else:
        image = indices_to_p_image(quantize(base, color_mode, dither), color_mode)

    draw = ImageDraw.Draw(image)
    if result.eco is not None:
        eco_px = [viewport.project(lat, lng) for lat, lng in result.eco.points]
        draw.line(eco_px, fill=colors["white"], width=(ECO_WIDTH + 2 * CASING) * scale, joint="curve")
        for piece in _dashed(eco_px, ECO_DASH * scale, ECO_GAP * scale):
            draw.line(piece, fill=colors["black"], width=ECO_WIDTH * scale, joint="curve")
    fast_px = [viewport.project(lat, lng) for lat, lng in result.fastest.points]
    draw.line(fast_px, fill=colors["white"], width=(FASTEST_WIDTH + 2 * CASING) * scale, joint="curve")
    draw.line(fast_px, fill=colors["red"], width=FASTEST_WIDTH * scale, joint="curve")

    ox, oy = fast_px[0]
    side_o = ORIGIN_SIDE * scale
    _paste_icon(image, colors, "trip_origin", side_o, int(round(ox)) - side_o // 2,
                int(round(oy)) - side_o // 2, "black", ICON_HALO * scale)
    dx, dy = fast_px[-1]
    side_d = DESTINATION_SIDE * scale
    # La punta del segnaposto è l'ultima riga d'inchiostro del glyph, non il bordo
    # del quadrato: è lei che deve cadere sul punto d'arrivo.
    tip = material_icons.mask("location_on", side_d).getbbox()[3]
    _paste_icon(image, colors, "location_on", side_d, int(round(dx)) - side_d // 2,
                int(round(dy)) - tip, "black", ICON_HALO * scale)

    card = draw_card(rows, colors, mode)
    if scale != 1:
        card = card.resize((card_w, card_h), Image.NEAREST)
    image.paste(card, (card_left, card_top))

    if fmt == "jpg":
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=92, subsampling=0, optimize=False, progressive=True)
        return buffer.getvalue()
    return encode_arduino(np.array(image, dtype=np.uint8), color_mode=color_mode)
