"""
Composizione dell'immagine del percorso: base cartografica, tracciati, marker e
riquadro informativo in alto a destra.

Lo stesso codice di disegno serve il JPEG a colori pieni e le immagini nella
palette e-paper, piani 1bpp o JPEG di anteprima: in queste la base si
quantizza prima, e tutto il resto si disegna dopo sugli indici di palette,
quindi testo, icone e tracciati restano in colore pieno senza errore di dither
attorno.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Callable, Hashable, Optional

import numpy as np
from PIL import Image, ImageDraw

import gfx_font
import material_icons
from arduino_format import encode as encode_arduino
from cache import TTLCache
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

CARD_PADDING = 6
CARD_ROW_GAP = 4
CARD_ICON_GAP = 5
CARD_COLUMN_GAP = 10
CARD_RADIUS = 8
CARD_BORDER = 2

# Oltre questo ritardo la riga del traffico passa al rosso.
DELAY_ALERT_S = 300

# Logo Google in basso a sinistra e attribuzione in basso a destra: misure
# abbondanti della resa a scale=1, che il tracciato non deve coprire; con lo zoom
# frazionario crescono con l'ingrandimento, come la mappa ridotta.
GOOGLE_LOGO = (72, 28)
GOOGLE_ATTRIBUTION = (210, 16)

# Qualità del JPEG a colori pieni, il formato di default di /route.
JPEG_QUALITY = 80

FONT_MAIN = "FreeSans17pt7b"
FONT_BODY = "FreeSans12pt7b"
FONT_SMALL = "FreeSans9pt7b"


@dataclass(frozen=True)
class CardItem:
    """Un elemento del riquadro: icona Material, testo ASCII, font GFX e colore."""

    icon: str
    text: str
    font: str
    color: str


@dataclass(frozen=True)
class CardRow:
    """
    Una riga del riquadro: elemento della colonna sinistra e della colonna destra,
    ciascuno assente se la riga non lo ha. Una riga senza elemento destro, la riga
    eco, parte dalla colonna sinistra e può occupare tutta la larghezza.
    """

    left: Optional[CardItem]
    right: Optional[CardItem] = None

    def items(self) -> list[CardItem]:
        return [item for item in (self.left, self.right) if item is not None]


@dataclass(frozen=True)
class CardItemKind:
    """Un elemento possibile del riquadro: icona Material, font GFX e testo di
    riserva, il più largo realistico che può avere con un tragitto sotto l'ora."""

    icon: str
    font: str
    reserve_text: str


# Gli elementi possibili per colonna. Nel riquadro stabile di /route i testi di
# riserva fissano le colonne anche quando il ritardo o la riga eco non ci sono.
DURATION_ITEM = CardItemKind("schedule", FONT_MAIN, "59 min")
DELAY_ITEM = CardItemKind("traffic", FONT_BODY, "+59 min")
UPDATE_ITEM = CardItemKind("update", FONT_SMALL, "agg. 88:88")
DISTANCE_ITEM = CardItemKind("straighten", FONT_BODY, "99,9 km")
ECO_ITEM = CardItemKind("eco", FONT_BODY, "+59 min -9,9 L")
LEFT_COLUMN_KINDS = (DURATION_ITEM, DELAY_ITEM)
RIGHT_COLUMN_KINDS = (UPDATE_ITEM, DISTANCE_ITEM)
FULL_ROW_KINDS = (ECO_ITEM,)
CARD_ITEM_KINDS = LEFT_COLUMN_KINDS + RIGHT_COLUMN_KINDS + FULL_ROW_KINDS

# Ultima viewport per percorso e misura: solo centro e zoom, nessun contenuto
# cartografico, quindi può durare ore senza toccare i termini di Google.
VIEWPORT_CACHE_TTL_S = 6 * 3600
VIEWPORT_CACHE_MAX_ENTRIES = 32
_viewport_cache = TTLCache(ttl_seconds=VIEWPORT_CACHE_TTL_S, max_entries=VIEWPORT_CACHE_MAX_ENTRIES)


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


def _item(kind: CardItemKind, text: str, color: str = "black") -> CardItem:
    """Elemento del riquadro di un tipo noto: icona e font vengono dal tipo."""
    return CardItem(kind.icon, text, kind.font, color)


def card_rows(result: RouteResult) -> list[CardRow]:
    """Righe del riquadro nell'ordine di lettura: tempo stimato e ora del calcolo,
    ritardo e distanza, alternativa eco se esiste. Senza ritardo la distanza resta
    nella sua colonna."""
    fastest = result.fastest
    rows = [CardRow(_item(DURATION_ITEM, format_duration(fastest.duration_s)),
                    _item(UPDATE_ITEM, "agg. " + result.computed_at.strftime("%H:%M")))]
    delay = fastest.delay_s
    delay_item = None
    if delay is not None:
        delay_item = _item(DELAY_ITEM, "+" + format_duration(delay), "red" if delay >= DELAY_ALERT_S else "black")
    rows.append(CardRow(delay_item, _item(DISTANCE_ITEM, format_distance(fastest.distance_m))))
    eco = result.eco
    if eco is not None:
        text = "+" + format_duration(eco.duration_s - fastest.duration_s)
        if eco.fuel_microliters is not None and fastest.fuel_microliters is not None:
            saved = fastest.fuel_microliters - eco.fuel_microliters
            if saved > 0:
                text += " -" + format_liters(saved)
        rows.append(CardRow(_item(ECO_ITEM, text)))
    return rows


def _icon_side(font: gfx_font.GfxFont) -> int:
    """Lato dell'icona per una riga: poco più alta delle maiuscole, come le
    icone accanto al testo nelle linee guida Material, ma compatta perchè il
    riquadro copra meno mappa."""
    return int(round(font.cap_height * 1.2))


def _item_width(font_name: str, text: str, ink: bool = False) -> int:
    """Larghezza a scale=1 di un elemento: icona, spazio e testo. Con `ink` il testo
    misura il suo inchiostro, altrimenti l'avanzata, che non cambia di un pixel o
    due da una cifra all'altra."""
    font = gfx_font.load(font_name)
    text_width = font.text_bounds(text)[2] if ink else font.advance(text)
    return _icon_side(font) + CARD_ICON_GAP + text_width


def _row_height(row: CardRow) -> int:
    """Altezza di una riga: l'icona o le maiuscole più alte fra i suoi elementi."""
    return max(max(_icon_side(font), font.cap_height) for font in (gfx_font.load(i.font) for i in row.items()))


@dataclass(frozen=True)
class _CardGeometry:
    """Misura del riquadro a scale=1 e ascissa della colonna destra."""

    width: int
    height: int
    right_x: int


def _card_geometry(rows: list[CardRow], stable: bool) -> _CardGeometry:
    """
    Colonna sinistra, colonna destra e righe piene. La colonna sinistra e le righe
    piene misurano l'inchiostro del testo reale; la colonna destra l'avanzata,
    perchè è quella che la allinea a destra. Con `stable` ogni colonna prende anche
    le avanzate dei testi di riserva, così bordo e colonna destra non si muovono
    fra due richieste dello stesso percorso e un testo oltre la riserva, per
    esempio un tragitto di un'ora o più, allarga il riquadro.
    """
    ink = not stable
    left = [_item_width(r.left.font, r.left.text, ink) for r in rows if r.left and r.right]
    right = [_item_width(r.right.font, r.right.text) for r in rows if r.right]
    full = [_item_width(r.left.font, r.left.text, ink) for r in rows if r.left and not r.right]
    if stable:
        left += [_item_width(k.font, k.reserve_text) for k in LEFT_COLUMN_KINDS]
        right += [_item_width(k.font, k.reserve_text) for k in RIGHT_COLUMN_KINDS]
        full += [_item_width(k.font, k.reserve_text) for k in FULL_ROW_KINDS]
    right_width = max(right, default=0)
    two_columns = max(left, default=0) + (CARD_COLUMN_GAP + right_width if right_width else 0)
    width = max(two_columns, max(full, default=0)) + 2 * CARD_PADDING
    height = sum(_row_height(row) for row in rows) + CARD_ROW_GAP * (len(rows) - 1) + 2 * CARD_PADDING
    return _CardGeometry(width, height, width - CARD_PADDING - right_width)


def card_size(rows: list[CardRow], stable: bool = False) -> tuple[int, int]:
    """Larghezza e altezza del riquadro a scale=1; `stable` come in _card_geometry()."""
    geometry = _card_geometry(rows, stable)
    return geometry.width, geometry.height


def draw_card(rows: list[CardRow], colors: dict[str, object], mode: str,
              stable: bool = False) -> Image.Image:
    """
    Riquadro a scale=1 come immagine a sè, nel modo dell'immagine finale: card
    bianca ad angoli arrotondati, righe di elementi icona più testo su due colonne,
    con le icone di ogni colonna allo stesso x. Gli elementi di una riga stanno
    sulla stessa baseline, quella del font più alto.
    """
    geometry = _card_geometry(rows, stable)
    card = Image.new(mode, (geometry.width, geometry.height), colors["white"])
    if mode == "P":
        card.putpalette(_palette_bytes(colors))
    ImageDraw.Draw(card).rounded_rectangle((0, 0, geometry.width - 1, geometry.height - 1), radius=CARD_RADIUS,
                                           fill=colors["white"], outline=colors["black"], width=CARD_BORDER)
    y = CARD_PADDING
    for row in rows:
        row_height = _row_height(row)
        baseline = y + (row_height + max(gfx_font.load(i.font).cap_height for i in row.items())) // 2
        for item, x in ((row.left, CARD_PADDING), (row.right, geometry.right_x)):
            if item is None:
                continue
            font = gfx_font.load(item.font)
            side = _icon_side(font)
            card.paste(colors[item.color], (x, baseline - (side + font.cap_height) // 2),
                       material_icons.mask(item.icon, side))
            font.draw(card, x + side + CARD_ICON_GAP, baseline, item.text, colors[item.color])
        y += row_height + CARD_ROW_GAP
    return card


def _palette_bytes(colors: dict[str, object]) -> list[int]:
    """Palette RGB lunga 768 per un'immagine "P" disegnata a parte."""
    return list(colors["_palette"])


def _color_table(color_mode: str) -> tuple[str, dict[str, object]]:
    """Modo dell'immagine e valore di ogni colore logico: tupla RGB a colori
    pieni, indice di palette altrimenti. Senza rosso nella palette il rosso è nero."""
    if color_mode == "rgb":
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


@dataclass(frozen=True)
class RouteLayout:
    """
    Geometria dell'immagine, uguale per ogni palette e dither: scale del disegno,
    righe, posizione e tipo di misura del riquadro, viewport della mappa. Separata
    dal disegno perchè examples.py scarica una base sola per viewport e la riusa
    in tutte le celle.
    """

    scale: int
    rows: list[CardRow]
    card_left: int
    card_top: int
    card_w: int
    card_h: int
    viewport: Viewport
    stable_card: bool = False


def _stable_viewport(key: Hashable, fit: Callable[[Optional[Viewport]], Viewport]) -> Viewport:
    """
    Viewport dell'ultimo fit con la stessa chiave se ha ancora la scala più alta
    ammissibile e un centro ammissibile, altrimenti un fit nuovo; il risultato
    sostituisce la voce in cache e ne rinnova la scadenza. Evita che la mappa si
    sposti di un pixel fra due richieste dello stesso percorso.
    """
    computed: list[bool] = []

    def first_fit() -> Viewport:
        computed.append(True)
        return fit(None)

    viewport = _viewport_cache.get_or_compute(key, first_fit)
    if not computed:
        viewport = fit(viewport)
    _viewport_cache.set(key, viewport)
    return viewport


def route_layout(result: RouteResult, width: int, height: int,
                 route_key: Optional[Hashable] = None) -> RouteLayout:
    """Riquadro in alto a destra e fit della mappa con il tracciato fuori dalle
    zone vietate; ValueError se il riquadro non entra nella misura richiesta.
    Fino a MAX_SIZE px per lato lo zoom è frazionario, oltre resta intero.
    Con `route_key` (origine, destinazione, carburante e eco, che passa solo
    /route) l'immagine è stabile fra richieste: riquadro a larghezza fissa e
    viewport dell'ultima richiesta dello stesso percorso e della stessa misura,
    riusata finchè ha la scala più alta ammissibile. Senza (/example) il
    riquadro è quello del testo reale e il fit parte sempre da zero."""
    scale = scale_for(width, height)
    rows = card_rows(result)
    stable = route_key is not None
    card_w, card_h = (v * scale for v in card_size(rows, stable=stable))
    margin = MARGIN * scale
    if card_w + 2 * margin > width or card_h + 2 * margin > height:
        raise ValueError(f"misura {width}x{height} troppo piccola per il riquadro {card_w}x{card_h}")
    card_left, card_top = width - margin - card_w, margin
    card_zone = Rect(card_left, card_top, card_left + card_w - 1, card_top + card_h - 1).expanded(margin)

    def forbidden(magnification: float) -> list[Rect]:
        """Zone vietate a un ingrandimento: il riquadro lo disegniamo noi e non
        cresce, logo e attribuzione crescono con la mappa ridotta."""
        logo_w, logo_h = (round(v * scale * magnification) for v in GOOGLE_LOGO)
        attribution_w, attribution_h = (round(v * scale * magnification) for v in GOOGLE_ATTRIBUTION)
        return [card_zone,
                Rect(0, height - logo_h, logo_w, height - 1),
                Rect(width - attribution_w, height - attribution_h, width - 1, height - 1)]

    paths = [result.fastest.points] + ([result.eco.points] if result.eco else [])
    markers = _markers(result.fastest, scale)

    def fit(prefer: Optional[Viewport]) -> Viewport:
        return fit_viewport(paths, markers, width, height, forbidden, margin=margin,
                            padding=LINE_PADDING * scale, scale=scale, prefer=prefer, fractional=scale == 1)

    if route_key is None:
        viewport = fit(None)
    else:
        viewport = _stable_viewport((route_key, width, height, scale), fit)
    return RouteLayout(scale, rows, card_left, card_top, card_w, card_h, viewport, stable)


def compose_route_image(result: RouteResult, layout: RouteLayout, base: Image.Image,
                        color_mode: str, dither: str) -> Image.Image:
    """
    Immagine prima della codifica: "RGB" a colori pieni con color_mode "rgb",
    "P" nella palette e-paper altrimenti. `base` è la mappa del viewport di
    `layout` e non viene modificata, quindi si può riusare fra più chiamate.
    """
    scale = layout.scale
    viewport = layout.viewport
    mode, colors = _color_table(color_mode)
    if mode == "RGB":
        image = base.convert("RGB")
    else:
        # La base è la mappa nei colori di Google, convertita nella palette con il
        # dither richiesto: strade gialle e arancioni possono finire in rosso o in
        # giallo, come il tracciato.
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

    card = draw_card(layout.rows, colors, mode, layout.stable_card)
    if scale != 1:
        card = card.resize((layout.card_w, layout.card_h), Image.NEAREST)
    image.paste(card, (layout.card_left, layout.card_top))
    return image


def encode_jpeg(image: Image.Image) -> bytes:
    """JPEG di /route, qualità JPEG_QUALITY; lo usa anche examples.py per stimare
    il peso a piena misura."""
    buffer = io.BytesIO()
    # Pillow non scrive in JPEG un'immagine a palette.
    image.convert("RGB").save(buffer, format="JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=False, progressive=True)
    return buffer.getvalue()


def render_route_image(
    result: RouteResult,
    width: int,
    height: int,
    fmt: str,
    color_mode: str,
    dither: str,
    fetch_map: Callable[[Viewport], Image.Image],
    route_key: Optional[Hashable] = None,
) -> bytes:
    """
    Immagine finale: JPEG se fmt è "jpg", a colori pieni con color_mode "rgb" e
    nella palette e-paper altrimenti; con "arduino" i piani 1bpp
    header-less di arduino_format. ValueError se il riquadro non entra nella
    misura richiesta. `route_key` è quello di route_layout().
    """
    layout = route_layout(result, width, height, route_key)
    image = compose_route_image(result, layout, fetch_map(layout.viewport), color_mode, dither)
    if fmt == "jpg":
        return encode_jpeg(image)
    return encode_arduino(np.array(image, dtype=np.uint8), color_mode=color_mode)
