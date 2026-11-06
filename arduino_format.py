"""
Encoder binario header-less per consumo diretto da ESP32. Copia di
webapp/arduino_format.py, da tenere allineata: è il contratto col firmware.

Il body HTTP contiene SOLO i piani 1bpp MSB-packed concatenati, senza magic,
senza version, senza metadati: l'ESP32 ha gia' width/height/colors nella query
string, quindi ogni parsing lato client è ridondante. Questo è il formato
piu' efficiente possibile: stream -> buffer GxEPD2 con un readBytes().
"""

from __future__ import annotations

import numpy as np

from dithering import PLANE_ORDER, indices_to_channel_masks, pack_mask_msb


def encode(indices: np.ndarray, color_mode: str) -> bytes:
    """
    Concatena i piani 1bpp nel formato atteso dall'ESP32.

    Ordine piani deterministico (vedi PLANE_ORDER in dithering.py):
        bw   -> [black]
        bwr  -> [black, red]
        bwry -> [black, red, yellow]

    Parametri:
        indices: ndarray (h, w) uint8 con indici della palette (output di dithering.quantize).
        color_mode: "bw" | "bwr" | "bwry".

    Ritorna: bytes di lunghezza N_planes * ceil(width/8) * height.
    """
    masks = indices_to_channel_masks(indices, color_mode)
    order = PLANE_ORDER[color_mode]
    planes = [pack_mask_msb(masks[channel]) for channel in order]
    return b"".join(planes)
