"""
Font Adafruit GFX letti dai loro header .h e disegnati a 1 bit: sono gli stessi
file che il firmware compila, quindi il testo del riquadro ha gli stessi pixel
del resto del pannello. Parser portato da tools/layout_preview.py del firmware.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from PIL import Image


FONT_DIR = Path(__file__).resolve().parent / "assets" / "gfx"


class GfxFont:
    """Bitmap, glyph, intervallo dei caratteri e interlinea di un font GFX."""

    def __init__(self, path: Path):
        text = path.read_text(encoding="latin-1")
        bitmap_block = text[text.index("Bitmaps[]"):text.index("Glyphs[]")]
        bitmap_block = bitmap_block[bitmap_block.index("{") + 1:bitmap_block.index("}")]
        self.bitmap = [int(value, 16) for value in re.findall(r"0x[0-9A-Fa-f]{2}", bitmap_block)]
        glyph_block = text[text.index("Glyphs[]"):]
        glyph_block = glyph_block[glyph_block.index("{") + 1:]
        self.glyphs = [tuple(map(int, match)) for match in
                       re.findall(r"\{\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(-?\d+),\s*(-?\d+)\s*\}", glyph_block)]
        tail = re.search(r"0x([0-9A-Fa-f]{2}),\s*0x([0-9A-Fa-f]{2}),\s*(\d+)\s*\};", text[text.rindex("GFXfont"):])
        self.first, self.last = int(tail.group(1), 16), int(tail.group(2), 16)
        self.y_advance = int(tail.group(3))
        # Cap height come la intende il firmware: distanza fra la cima della "H" e
        # la baseline, cioè -yOffset; l'inchiostro occupa anche la riga della baseline.
        cap = self.glyph("H")
        self.cap_height = -cap[5] if cap else self.y_advance // 2

    def glyph(self, char: str):
        """Glyph del carattere, None fuori dall'intervallo come in Adafruit_GFX."""
        code = ord(char)
        if code < self.first or code > self.last:
            return None
        return self.glyphs[code - self.first]

    def text_bounds(self, text: str) -> tuple[int, int, int, int]:
        """getTextBounds() da (0, 0): riquadro d'inchiostro (x1, y1, w, h) rispetto
        al cursore sulla baseline."""
        min_x = min_y = 10 ** 6
        max_x = max_y = -(10 ** 6)
        cursor = 0
        for char in text:
            glyph = self.glyph(char)
            if glyph is None:
                continue
            _, gw, gh, advance, xo, yo = glyph
            if gw and gh:
                min_x, min_y = min(min_x, cursor + xo), min(min_y, yo)
                max_x, max_y = max(max_x, cursor + xo + gw - 1), max(max_y, yo + gh - 1)
            cursor += advance
        if max_x < min_x:
            return 0, 0, 0, 0
        return min_x, min_y, max_x - min_x + 1, max_y - min_y + 1

    def advance(self, text: str) -> int:
        """Avanzamento del cursore dopo la stringa."""
        total = 0
        for char in text:
            glyph = self.glyph(char)
            if glyph is not None:
                total += glyph[3]
        return total

    def mask(self, text: str) -> tuple[Image.Image, int, int]:
        """
        Maschera "1" dell'inchiostro della stringa e offset (x1, y1) del suo angolo
        rispetto al cursore sulla baseline, come print() di Adafruit_GFX.
        """
        x1, y1, w, h = self.text_bounds(text)
        image = Image.new("1", (max(w, 1), max(h, 1)), 0)
        pixels = image.load()
        cursor = 0
        for char in text:
            glyph = self.glyph(char)
            if glyph is None:
                continue
            offset, gw, gh, advance, xo, yo = glyph
            bits = bit = 0
            for yy in range(gh):
                for xx in range(gw):
                    if not (bit & 7):
                        bits = self.bitmap[offset]
                        offset += 1
                    bit += 1
                    if bits & 0x80:
                        pixels[cursor + xo + xx - x1, yo + yy - y1] = 1
                    bits = (bits << 1) & 0xFF
            cursor += advance
        return image, x1, y1

    def draw(self, image: Image.Image, x: int, baseline: int, text: str, fill) -> None:
        """Scrive la stringa con il cursore in (x, baseline), nel colore `fill`
        del modo dell'immagine (indice di palette o tupla RGB)."""
        mask, x1, y1 = self.mask(text)
        image.paste(fill, (x + x1, baseline + y1), mask)


@lru_cache(maxsize=None)
def load(name: str) -> GfxFont:
    """Font per nome del file senza estensione, per esempio "FreeSans12pt7b"."""
    return GfxFont(FONT_DIR / f"{name}.h")
