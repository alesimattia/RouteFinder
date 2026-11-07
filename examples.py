"""
Griglia di /example: la stessa immagine del percorso in ogni palette e dither
del pannello, più la versione a colori pieni, in un'unica PNG grande quanto la
viewport del browser, con tempo, RAM e peso di ciascuna cella.

Le celle sono piccole quanto basta a stare nella tela, così la RAM resta quella
di una richiesta /route a quella misura. Le API Google si chiamano una volta
sola per richiesta: il percorso arriva già calcolato da main.py, e la Static
API serve una sola base nei colori di Google, che la cella rgb mostra così
com'è e le altre convertono nella loro palette col loro dither. Ogni cella è
identica a quella che /route restituisce con gli stessi parametri, perchè
passa dalle stesse funzioni di renderer.py.
"""

from __future__ import annotations

import io
import math
import re
import threading
import time
import tracemalloc
from dataclasses import dataclass
from typing import Callable, Optional

from PIL import Image, ImageDraw

import gfx_font
import renderer
from dithering import PLANE_ORDER
from map_fit import Viewport
from routes_api import RouteResult
from static_map import MAX_SCALE, MAX_SIDE


# Percorso fisso degli esempi, nel formato "lat,lng" della querystring di /route:
# main.py lo valida con parse_point() come una richiesta normale.
ORIGIN = "43.5689926,13.5132431"
DESTINATION = "43.5572068,13.5074924"

# Tela senza viewport (curl, script) e tela massima: oltre, le celle
# crescerebbero e con loro la RAM, ~180 byte per pixel con Atkinson (~55 MB
# per una cella da 630x466, quella della tela massima).
DEFAULT_CANVAS = (1920, 1080)
MAX_CANVAS = (2560, 1600)

# Celle in ordine di lettura come (colors, dither); None è il riepilogo. Su
# quattro colonne viene una riga per palette e una colonna per dither. Atkinson
# è in Python puro e costa ~20 volte gli altri, quindi compare una volta sola.
CELLS: tuple[Optional[tuple[str, str]], ...] = (
    ("bw", "no"), ("bw", "floyd"), ("bw", "bayer"), ("rgb", "no"),
    ("bwr", "no"), ("bwr", "floyd"), ("bwr", "bayer"), ("bwry", "atkinson"),
    ("bwry", "no"), ("bwry", "floyd"), ("bwry", "bayer"), None,
)

# Proporzioni della fascia del 9.7" nel firmware: fra due disposizioni vince
# quella in cui un'immagine di queste proporzioni viene più grande, così le
# celle somigliano a ciò che il pannello mostra.
PANEL_ASPECT = 610 / 290

GUTTER = 8
PADDING = 6
BACKGROUND = (220, 220, 220)
TEXT = (0, 0, 0)
FONT_TITLE = renderer.FONT_BODY
FONT_TEXT = renderer.FONT_SMALL
STATS_SEPARATOR = "  "

# Didascalia più larga che una cella può avere: dimensiona lo spazio sotto le
# celle prima di misurarle.
_WIDEST_CAPTION = ("&colors=bwry&dither=atkinson",
                   ["999 ms", "RAM 99,99 MB", f"{MAX_SIDE}: ~9,99 MB"])

# Pagina che il browser riceve senza width e height: rilegge lo stesso URL con
# la viewport in pixel CSS, non quelli fisici dello schermo, così l'immagine la
# riempie senza scorrere. La query si accoda così com'è, senza ricodificarla.
VIEWPORT_PAGE = """<!doctype html>
<html lang="it">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Routefinder, esempi di dither</title></head>
<body><script>
location.replace(location.pathname + location.search + (location.search ? "&" : "?")
  + "width=" + window.innerWidth + "&height=" + window.innerHeight);
</script>
<noscript>Senza JavaScript aggiungi all'URL &amp;width= e &amp;height= della finestra.</noscript></body>
</html>
"""

# tracemalloc è globale al processo: due misure insieme si sporcherebbero a vicenda.
_TRACE_LOCK = threading.Lock()


@dataclass(frozen=True)
class Grid:
    """Disposizione delle celle nella tela: colonne e righe, misura della mappa
    di ogni cella e della didascalia sotto, geometria del percorso comune a
    tutte le celle."""

    canvas: tuple[int, int]
    columns: int
    rows: int
    cell_w: int
    cell_h: int
    caption_h: int
    route: renderer.RouteLayout

    def origin(self, index: int) -> tuple[int, int]:
        """Angolo in alto a sinistra della mappa della cella `index`, con la
        griglia centrata nella tela; usato anche dai test."""
        used_w = self.columns * (self.cell_w + GUTTER) + GUTTER
        used_h = self.rows * (self.cell_h + self.caption_h + GUTTER) + GUTTER
        col, row = index % self.columns, index // self.columns
        return ((self.canvas[0] - used_w) // 2 + GUTTER + col * (self.cell_w + GUTTER),
                (self.canvas[1] - used_h) // 2 + GUTTER + row * (self.cell_h + self.caption_h + GUTTER))


def _seconds(value: float) -> str:
    """Durata in millisecondi sotto il secondo, con un decimale sotto i 10 ms,
    altrimenti in secondi; sempre con la virgola."""
    if value < 0.01:
        return f"{value * 1000:.1f}".replace(".", ",") + " ms"
    if value < 1:
        return f"{value * 1000:.0f} ms"
    return f"{value:.2f}".replace(".", ",") + " s"


def _megabytes(value: float) -> str:
    """Byte in MB decimali, con la virgola."""
    return f"{value / 1e6:.2f}".replace(".", ",") + " MB"


def _pack(items: list[str], max_width: int, separator: str) -> list[str]:
    """Elementi in righe larghe al più max_width nel font del testo; un elemento
    più largo della riga resta da solo e si taglia al bordo della cella."""
    font = gfx_font.load(FONT_TEXT)
    lines: list[str] = []
    for item in items:
        if lines and font.advance(lines[-1] + separator + item) <= max_width:
            lines[-1] += separator + item
        else:
            lines.append(item)
    return lines


def _query_lines(url: str, max_width: int) -> list[str]:
    """Querystring spezzata prima di ogni "&" dove la riga finisce."""
    return _pack(re.split(r"(?=&)", url), max_width, "")


def _caption_lines(suffix: str, stats: list[str], width: int) -> list[str]:
    """Didascalia di una cella larga width: il suffisso dell'URL /route, poi le misure."""
    usable = width - 2 * PADDING
    return _query_lines(suffix, usable) + _pack(stats, usable, STATS_SEPARATOR)


def _caption_height(width: int) -> int:
    """Spazio sotto una cella larga width, sulla didascalia più larga possibile."""
    lines = _caption_lines(*_WIDEST_CAPTION, width)
    return 2 * PADDING + len(lines) * gfx_font.load(FONT_TEXT).y_advance


def _panel_area(width: int, height: int) -> float:
    """Area del rettangolo più grande con le proporzioni PANEL_ASPECT che entra
    in una cella width x height."""
    return min(width, height * PANEL_ASPECT) * min(height, width / PANEL_ASPECT)


def grid_for(result: RouteResult, canvas_w: int, canvas_h: int) -> Grid:
    """
    Fra tutti i numeri di colonne, la disposizione con le celle più vicine al
    pannello (_panel_area più grande) in cui il riquadro informativo di /route
    entra; ValueError se nessuna entra nella tela. Le celle restano entro il
    lato massimo di /route, così ognuna si può riprodurre da lì.
    """
    best: Optional[Grid] = None
    for columns in range(1, len(CELLS) + 1):
        rows = math.ceil(len(CELLS) / columns)
        cell_w = (canvas_w - (columns + 1) * GUTTER) // columns
        caption_h = _caption_height(cell_w)
        cell_h = (canvas_h - (rows + 1) * GUTTER) // rows - caption_h
        if best is not None and _panel_area(cell_w, cell_h) <= _panel_area(best.cell_w, best.cell_h):
            continue
        try:
            route = renderer.route_layout(result, cell_w, cell_h)
        except ValueError:
            continue
        best = Grid((canvas_w, canvas_h), columns, rows, cell_w, cell_h, caption_h, route)
    if best is None:
        raise ValueError(f"tela {canvas_w}x{canvas_h} troppo piccola: in nessuna disposizione "
                         f"delle {len(CELLS)} celle entra il riquadro di /route")
    return best


def _peak_allocations(cells: list[tuple[str, str]],
                      compose: Callable[[tuple[str, str]], Image.Image]) -> dict[tuple[str, str], int]:
    """
    Picco di memoria allocata durante la composizione di ogni cella, misurato
    con tracemalloc: vede gli oggetti Python e gli array numpy, non i buffer
    delle immagini Pillow. Passata separata da quella dei tempi, perchè
    tracemalloc rallenta Atkinson di circa 20 volte.
    """
    peaks = {}
    with _TRACE_LOCK:
        tracemalloc.start()
        try:
            for cell in cells:
                tracemalloc.reset_peak()
                before = tracemalloc.get_traced_memory()[0]
                compose(cell)
                peaks[cell] = tracemalloc.get_traced_memory()[1] - before
        finally:
            tracemalloc.stop()
    return peaks


def _full_size_jpeg(image: Image.Image, scale: int) -> float:
    """
    Stima del JPEG di /route a MAX_SIDE x MAX_SIDE, senza generarlo: a quella
    misura la Static API lavora a scale=2, che disegna gli stessi dettagli con
    il doppio dei pixel, quindi la cella si porta a scale=2 e il peso si scala
    sui pixel. Su una mappa reale la stima è risultata del 20% sopra il vero; la
    proporzione semplice sui pixel della cella darebbe il doppio.
    """
    factor = MAX_SCALE // scale
    if factor > 1:
        image = image.resize((image.width * factor, image.height * factor), Image.LANCZOS)
    return len(renderer.encode_jpeg(image)) * MAX_SIDE * MAX_SIDE / (image.width * image.height)


def render_examples(
    result: RouteResult,
    canvas_w: int,
    canvas_h: int,
    route_query: str,
    routes_s: Optional[float],
    fetch_map: Callable[[Viewport], Image.Image],
) -> bytes:
    """
    PNG da canvas_w x canvas_h con le celle di CELLS nella disposizione di
    grid_for(). `route_query` è la richiesta /route senza misura che il
    riepilogo mostra, `routes_s` la durata della chiamata alla Routes API (None
    se il percorso era in cache), `fetch_map(viewport)` la Static API.
    ValueError se la tela è troppo piccola.
    """
    grid = grid_for(result, canvas_w, canvas_h)
    layout = grid.route
    started = time.perf_counter()
    base = fetch_map(layout.viewport)
    static_s = time.perf_counter() - started

    cells = [cell for cell in CELLS if cell is not None]

    def compose(cell: tuple[str, str]) -> Image.Image:
        colors, dither = cell
        return renderer.compose_route_image(result, layout, base, colors, dither)

    # La passata della RAM va per prima e scalda le cache di font e icone, che
    # altrimenti finirebbero nel tempo della prima cella.
    peaks = _peak_allocations(cells, compose)
    images: dict[tuple[str, str], Image.Image] = {}
    elapsed: dict[tuple[str, str], float] = {}
    for cell in cells:
        started = time.perf_counter()
        images[cell] = compose(cell)
        elapsed[cell] = time.perf_counter() - started

    def caption(cell: tuple[str, str]) -> list[str]:
        colors, dither = cell
        if colors == "rgb":
            suffix = "&format=jpg (rgb)"
            weight = f"~{_megabytes(_full_size_jpeg(images[cell], layout.scale))}"
        else:
            suffix = f"&colors={colors}&dither={dither}"
            weight = _megabytes(len(PLANE_ORDER[colors]) * ((MAX_SIDE + 7) // 8) * MAX_SIDE)
        stats = [_seconds(elapsed[cell]), f"RAM {_megabytes(peaks[cell])}", f"{MAX_SIDE}: {weight}"]
        return _caption_lines(suffix, stats, grid.cell_w)

    usable = grid.cell_w - 2 * PADDING
    route_url = f"{route_query}&width={grid.cell_w}&height={grid.cell_h}"
    # Dal più al meno importante: in una cella bassa le ultime righe si perdono.
    routes = f"Routes {_seconds(routes_s)}" if routes_s is not None else "Routes in cache"
    info = [
        "+ suffisso della cella",
        f"{routes}, Static {_seconds(static_s)}",
        f"zoom {layout.viewport.zoom}, scale {layout.scale}, {len(cells)} celle in {_seconds(sum(elapsed.values()))}",
        f"{MAX_SIDE}: /route a {MAX_SIDE}x{MAX_SIDE}, ~ stima",
        "RAM: Python e numpy (tracemalloc)",
    ]
    summary = [(FONT_TITLE, "Riepilogo")]
    summary += [(FONT_TEXT, line) for line in _query_lines(route_url, usable)]
    summary += [(FONT_TEXT, line) for text in info for line in _pack(text.split(" "), usable, " ")]

    canvas = Image.new("RGB", grid.canvas, BACKGROUND)
    tile_h = grid.cell_h + grid.caption_h
    for index, cell in enumerate(CELLS):
        tile = Image.new("RGB", (grid.cell_w, tile_h), (255, 255, 255))
        if cell is None:
            lines = summary
            top = PADDING
        else:
            tile.paste(images[cell].convert("RGB"), (0, 0))
            ImageDraw.Draw(tile).line([(0, grid.cell_h), (grid.cell_w - 1, grid.cell_h)], fill=BACKGROUND)
            lines = [(FONT_TEXT, line) for line in caption(cell)]
            top = grid.cell_h + PADDING
        # Righe dall'alto, ciascuna alla sua interlinea; ciò che esce dalla cella
        # si perde, così il testo non sconfina nella vicina.
        for font_name, text in lines:
            font = gfx_font.load(font_name)
            font.draw(tile, PADDING, top + font.cap_height, text, TEXT)
            top += font.y_advance
        canvas.paste(tile, grid.origin(index))

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()
