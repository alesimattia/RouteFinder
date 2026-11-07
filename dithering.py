"""
Quantizzazione e dithering per display e-paper.

Copia di webapp/dithering.py, da tenere allineata. Port semplificato delle funzioni da ../GxEPD2_SOLUM_ESL/tools/epd_image_converter.pyw, senza dipendenze
Tk/GUI. Espone i 4 algoritmi di dithering (Floyd-Steinberg, Atkinson, Bayer 8x8,
threshold) e le utility di packing MSB-first compatibili con GxEPD2.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from PIL import Image


# Palette RGB indicizzate. Indice 0 = bianco (sempre), indice 1 = nero.
PALETTES: dict[str, list[tuple[int, int, int]]] = {
    "bw":   [(255, 255, 255), (0, 0, 0)],
    "bwr":  [(255, 255, 255), (0, 0, 0), (255, 0, 0)],
    "bwry": [(255, 255, 255), (0, 0, 0), (255, 0, 0), (255, 255, 0)],
}

# Ordine piani restituiti per ogni color_mode (fondamentale per il parser ESP32).
PLANE_ORDER: dict[str, tuple[str, ...]] = {
    "bw":   ("black",),
    "bwr":  ("black", "red"),
    "bwry": ("black", "red", "yellow"),
}


BAYER_8X8 = np.array([
    [ 0, 32,  8, 40,  2, 34, 10, 42],
    [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44,  4, 36, 14, 46,  6, 38],
    [60, 28, 52, 20, 62, 30, 54, 22],
    [ 3, 35, 11, 43,  1, 33,  9, 41],
    [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47,  7, 39, 13, 45,  5, 37],
    [63, 31, 55, 23, 61, 29, 53, 21],
], dtype=np.float32)


@lru_cache(maxsize=8)
def _load_palette_image_cached(palette_rgb: tuple) -> Image.Image:
    """
    Costruisce un'immagine Pillow in modalita' 'P' con la palette indicata.

    Cacheata per palette tuple: viene chiamata ad ogni quantize, e con sole
    3 palette possibili (bw/bwr/bwry) il cache hit-rate è del 100% dopo il
    primo uso. Restituisce la stessa Image: Pillow .quantize() la usa
    read-only quindi va bene condividerla.
    """
    palette_data = []
    for rgb in palette_rgb:
        palette_data.extend(rgb)
    while len(palette_data) < 768:
        palette_data.extend((0, 0, 0))
    pal = Image.new("P", (1, 1))
    pal.putpalette(palette_data)
    return pal


def _load_palette_image(palette_rgb):
    """Wrapper che normalizza palette_rgb a tuple per il caching."""
    return _load_palette_image_cached(tuple(tuple(c) for c in palette_rgb))


def _quantize_floyd_steinberg(img_rgb, palette_rgb):
    """Floyd-Steinberg nativo Pillow (veloce, C-implementato)."""
    pal_img = _load_palette_image(palette_rgb)
    quantized = img_rgb.quantize(palette=pal_img, dither=Image.Dither.FLOYDSTEINBERG)
    return np.array(quantized, dtype=np.uint8)


def _quantize_threshold(img_rgb, palette_rgb):
    """Nearest neighbor senza dither."""
    pal_img = _load_palette_image(palette_rgb)
    quantized = img_rgb.quantize(palette=pal_img, dither=Image.Dither.NONE)
    return np.array(quantized, dtype=np.uint8)


@lru_cache(maxsize=8)
def _bayer_tile(h: int, w: int) -> np.ndarray:
    """
    Tile della matrice di Bayer 8x8 normalizzata, croppato a (h, w).

    Cacheato per (h, w): _quantize_bayer somma poi questo tile (read-only)
    al canvas perturbato — non lo muta — quindi e' sicuro condividerlo tra
    chiamate. Su 600x800 il tile pesa ~1.9MB float32: cachearlo evita
    l'allocazione ad ogni richiesta dithered Bayer.
    """
    bayer_norm = (BAYER_8X8 + 0.5) / 64.0 - 0.5
    tiles_y = (h + 7) // 8
    tiles_x = (w + 7) // 8
    return np.tile(bayer_norm, (tiles_y, tiles_x))[:h, :w]


def _quantize_bayer(img_rgb, palette_rgb):
    """Dithering ordinato su matrice di Bayer 8x8, pattern stabile nel tempo."""
    arr = np.array(img_rgb, dtype=np.float32)
    h, w, _ = arr.shape
    bayer_tiled = _bayer_tile(h, w)
    amplitude = 64.0
    perturbed = arr + (bayer_tiled[:, :, None] * amplitude)
    palette = np.array(palette_rgb, dtype=np.float32)
    flat = perturbed.reshape(-1, 3)
    diffs = flat[:, None, :] - palette[None, :, :]
    dists = np.sum(diffs * diffs, axis=2)
    idx = np.argmin(dists, axis=1).astype(np.uint8)
    return idx.reshape(h, w)


def _quantize_atkinson(img_rgb, palette_rgb):
    """
    Dithering Atkinson: distribuisce 6/8 dell'errore a 6 vicini.

    L'errore di un pixel va a (y, x+1), (y, x+2), (y+1, x-1), (y+1, x),
    (y+1, x+1) e (y+2, x), quindi i pixel con lo stesso t = x + 2y non
    dipendono l'uno dall'altro: si elaborano insieme in numpy, un'antidiagonale
    alla volta, w + 2h passi invece di un ciclo Python su ogni pixel. Ogni pixel
    somma gli errori ricevuti nell'ordine della scansione riga per riga, quindi
    il risultato è identico bit per bit a quello del ciclo sui pixel, con una
    frazione del tempo e della RAM (niente liste annidate di float Python).
    """
    base = np.asarray(img_rgb, dtype=np.float32)
    h, w, _ = base.shape
    palette = np.array(palette_rgb, dtype=np.float64)
    # Errore già diviso per 8 di ogni pixel, con due righe e due colonne vuote
    # prima e una colonna dopo: i vicini fuori dall'immagine contribuiscono 0.
    err = np.zeros((h + 2, w + 3, 3), dtype=np.float64)
    result = np.empty((h, w), dtype=np.uint8)
    for t in range(w + 2 * (h - 1)):
        ys = np.arange(max(0, (t - w + 2) // 2), min(h - 1, t // 2) + 1)
        xs = t - 2 * ys
        ey, ex = ys + 2, xs + 2
        p = base[ys, xs].astype(np.float64)
        p = p + err[ey - 2, ex]
        p = p + err[ey - 1, ex - 1]
        p = p + err[ey - 1, ex]
        p = p + err[ey - 1, ex + 1]
        p = p + err[ey, ex - 2]
        p = p + err[ey, ex - 1]
        diff = p[:, None, :] - palette[None, :, :]
        square = diff * diff
        idx = np.argmin(square[..., 0] + square[..., 1] + square[..., 2], axis=1)
        result[ys, xs] = idx
        err[ey, ex] = (p - palette[idx]) * 0.125
    return result


_DISPATCH = {
    "no": _quantize_threshold,
    "floyd": _quantize_floyd_steinberg,
    "atkinson": _quantize_atkinson,
    "bayer": _quantize_bayer,
}


def quantize(img_rgb: Image.Image, color_mode: str, dither: str) -> np.ndarray:
    """
    Quantizza l'immagine RGB agli indici della palette per color_mode.

    Parametri:
        img_rgb: Pillow Image RGB.
        color_mode: "bw" | "bwr" | "bwry".
        dither: "no" | "floyd" | "atkinson" | "bayer".

    Ritorna: ndarray uint8 di shape (h, w) con indici in [0, len(palette)-1].
    """
    if color_mode not in PALETTES:
        raise ValueError(f"color_mode non valido: {color_mode!r}")
    if dither not in _DISPATCH:
        raise ValueError(f"dither non valido: {dither!r}")
    palette = PALETTES[color_mode]
    return _DISPATCH[dither](img_rgb.convert("RGB"), palette)


def indices_to_rgb(indices: np.ndarray, color_mode: str) -> np.ndarray:
    """Rimappa gli indici a un array HxWx3 uint8 per preview umano."""
    palette = np.array(PALETTES[color_mode], dtype=np.uint8)
    return palette[indices]


def indices_to_p_image(indices: np.ndarray, color_mode: str) -> Image.Image:
    """
    Costruisce un'Image Pillow "P" (paletted) dagli indici di palette.

    Pillow gestisce la conversione P->RGB internamente al save JPEG, quindi
    NON serve materializzare un array HxWx3 intermedio. Risparmia ~1.5MB
    di allocazione per 600x800 e una conversione ndarray->PIL.
    """
    palette = PALETTES[color_mode]
    img = Image.fromarray(indices, mode="P")
    palette_data = []
    for rgb in palette:
        palette_data.extend(rgb)
    while len(palette_data) < 768:
        palette_data.extend((0, 0, 0))
    img.putpalette(palette_data)
    return img


def pack_mask_msb(mask: np.ndarray) -> bytes:
    """
    Impacca una mask booleana 2D in bytes MSB-first, riga per riga.

    Righe paddate a multiplo di 8 pixel con bit=1 (compatibile con GxEPD2:
    bit=1 significa pixel NON appartenente al canale, cioè bianco/default).
    """
    h, w = mask.shape
    pad_w = (w + 7) // 8 * 8
    if pad_w != w:
        padded = np.ones((h, pad_w), dtype=bool)
        padded[:, :w] = mask
        mask = padded
    reshaped = mask.reshape(h, pad_w // 8, 8)
    weights = np.array([128, 64, 32, 16, 8, 4, 2, 1], dtype=np.uint8)
    packed = (reshaped.astype(np.uint8) * weights).sum(axis=2).astype(np.uint8)
    return packed.flatten().tobytes()


def indices_to_channel_masks(indices: np.ndarray, color_mode: str) -> dict[str, np.ndarray]:
    """
    Converte gli indici di palette in maschere booleane per canale.

    Convenzione (compatibile GxEPD2 writeImage(black, color)):
        mask[canale] = True dove il pixel NON ha quel colore.
    """
    if color_mode == "bw":
        return {"black": (indices == 0)}
    if color_mode == "bwr":
        return {
            "black": (indices != 1),
            "red":   (indices != 2),
        }
    if color_mode == "bwry":
        return {
            "black":  (indices != 1),
            "red":    (indices != 2),
            "yellow": (indices != 3),
        }
    raise ValueError(f"color_mode non valido: {color_mode!r}")
