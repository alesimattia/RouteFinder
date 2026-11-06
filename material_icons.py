"""
Icone Material Symbols Rounded rese a 1 bit, dallo stesso font variabile delle
icone meteo del firmware (icons/weather/source), con la stessa soglia a 128 di
tools/weather_icons.py: niente grigi da diffondere attorno all'icona.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ASSET_DIR = Path(__file__).resolve().parent / "assets" / "material"
FONT_PATH = ASSET_DIR / "MaterialSymbolsRounded.ttf"
CODEPOINTS_PATH = ASSET_DIR / "MaterialSymbolsRounded.codepoints"

# Assi Fill, Grade, Optical Size, Weight. Vuote come le icone meteo; optical
# size al minimo del font perchè le icone del riquadro stanno fra 16 e 30 px, e
# peso 500 perchè a quella misura il tratto da 400 si spezza a 1 bit.
AXES = (0, 0, 20, 500)


@lru_cache(maxsize=1)
def _codepoints() -> dict[str, int]:
    """Nome dell'icona -> codepoint, dal file che accompagna il font."""
    table = {}
    for line in CODEPOINTS_PATH.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2:
            table[parts[0]] = int(parts[1], 16)
    return table


@lru_cache(maxsize=16)
def _font(size: int) -> ImageFont.FreeTypeFont:
    """Font variabile alla misura in pixel, con gli assi fissati."""
    font = ImageFont.truetype(str(FONT_PATH), size)
    font.set_variation_by_axes(list(AXES))
    return font


@lru_cache(maxsize=64)
def mask(name: str, side: int) -> Image.Image:
    """
    Maschera "1" quadrata di lato `side` con l'icona centrata sulle metriche del
    font, così la posizione resta quella disegnata da Material.
    """
    codepoints = _codepoints()
    if name not in codepoints:
        raise KeyError(f"icona Material sconosciuta: {name}")
    canvas = Image.new("L", (side, side), 0)
    ImageDraw.Draw(canvas).text((side / 2, side / 2), chr(codepoints[name]),
                                font=_font(side), fill=255, anchor="mm")
    return canvas.point(lambda value: 255 if value >= 128 else 0).convert("1")


def halo(icon_mask: Image.Image, radius: int) -> Image.Image:
    """Maschera dell'icona allargata di `radius` pixel, per il bordo bianco che la
    stacca dalla mappa; la tela cresce di `radius` per lato."""
    side_w, side_h = icon_mask.width + 2 * radius, icon_mask.height + 2 * radius
    canvas = Image.new("L", (side_w, side_h), 0)
    canvas.paste(255, (radius, radius), icon_mask)
    return canvas.filter(ImageFilter.MaxFilter(2 * radius + 1)).point(lambda v: 255 if v else 0).convert("1")
