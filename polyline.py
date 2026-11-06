"""
Decodifica dell'encoded polyline di Google, l'algoritmo standard a precisione 5
che la Routes API usa per routes.polyline.encodedPolyline.
"""

from __future__ import annotations


def decode(encoded: str) -> list[tuple[float, float]]:
    """
    Restituisce i punti (lat, lng) della polilinea, in gradi decimali.

    Ogni coordinata è un delta zig-zag a gruppi di 5 bit dal punto precedente:
    un carattere fuori dall'alfabeto o una stringa troncata a metà di un valore
    sono un errore, perchè disegnerebbero un tracciato sbagliato senza dirlo.
    """
    points: list[tuple[float, float]] = []
    index = 0
    lat = 0
    lng = 0
    length = len(encoded)
    while index < length:
        deltas = []
        for _ in range(2):
            result = 0
            shift = 0
            while True:
                if index >= length:
                    raise ValueError("polilinea troncata a metà di una coordinata")
                byte = ord(encoded[index]) - 63
                index += 1
                if byte < 0 or byte > 0x3F + 0x20:
                    raise ValueError(f"carattere non valido nella polilinea: {encoded[index - 1]!r}")
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lng += deltas[1]
        points.append((lat / 1e5, lng / 1e5))
    return points
