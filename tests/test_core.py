"""Test della logica pura: polilinea, proiezione e fit, parsing della Routes API,
formato arduino e testo del riquadro."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from PIL import Image

import dithering
import examples
import gfx_font
import material_icons
import polyline
import renderer
import routes_api
import static_map
from dithering import PALETTES
from map_fit import Marker, Rect, Viewport, fit_viewport, world_to_latlng, world_xy


def test_polyline_decodes_google_reference_example():
    # Esempio della pagina ufficiale dell'algoritmo.
    points = polyline.decode("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    assert points == [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]


def test_polyline_rejects_truncated_string():
    with pytest.raises(ValueError):
        polyline.decode("_p~iF~ps|")


def test_world_projection_round_trip():
    for lat, lng in [(43.6158, 13.5189), (-33.9, 151.2), (0.0, 0.0)]:
        back = world_to_latlng(*world_xy(lat, lng))
        assert back == pytest.approx((lat, lng), abs=1e-9)


def _diagonal(n=40, start=(43.55, 13.40), end=(43.65, 13.60)):
    """Tracciato da sud-ovest a nord-est, cioè verso il riquadro in alto a destra."""
    return [(start[0] + (end[0] - start[0]) * i / (n - 1), start[1] + (end[1] - start[1]) * i / (n - 1))
            for i in range(n)]


def _drawn_pixels(viewport: Viewport, path):
    return [viewport.project(lat, lng) for lat, lng in path]


@pytest.mark.parametrize("scale,width,height", [(1, 610, 290), (1, 610, 325), (2, 1220, 580)])
def test_fit_keeps_diagonal_route_out_of_forbidden_zones(scale, width, height):
    path = _diagonal()
    card = Rect(width - 200 * scale, 6 * scale, width - 6 * scale, 140 * scale)
    logo = Rect(0, height - 28 * scale, 72 * scale, height - 1)
    padding = 6 * scale
    viewport = fit_viewport([path], [], width, height, [card, logo], margin=6 * scale,
                            padding=padding, scale=scale)
    for x, y in _drawn_pixels(viewport, path):
        assert 0 <= x < width and 0 <= y < height
        for rect in (card, logo):
            r = rect.expanded(padding)
            # Tolleranza di un pixel per l'arrotondamento del centro a sei decimali.
            assert not (r.x0 + 1 < x < r.x1 - 1 and r.y0 + 1 < y < r.y1 - 1)


def test_fit_prefers_highest_zoom_that_fits():
    path = _diagonal(start=(43.600, 13.500), end=(43.602, 13.503))
    viewport = fit_viewport([path], [], 610, 290, [], margin=6, padding=6)
    assert viewport.zoom >= 16
    tighter = fit_viewport([path], [], 610, 290, [], margin=6, padding=6, max_zoom=viewport.zoom + 1)
    assert tighter.zoom == viewport.zoom


def test_fit_accounts_for_marker_extent():
    path = [(43.60, 13.50), (43.60, 13.50)]
    marker = Marker(43.60, 13.50, -13, -28, 13, 2)
    viewport = fit_viewport([path], [marker], 610, 290, [], margin=6, padding=0)
    x, y = viewport.project(43.60, 13.50)
    assert y - 28 >= 6 - 1


def _route_payload(same_eco=False):
    fast = polyline_encode([(43.60, 13.50), (43.61, 13.52), (43.62, 13.55)])
    eco = fast if same_eco else polyline_encode([(43.60, 13.50), (43.59, 13.53), (43.62, 13.55)])
    routes = [{
        "duration": "1500s", "staticDuration": "1200s", "distanceMeters": 18400,
        "polyline": {"encodedPolyline": fast},
        "routeLabels": ["DEFAULT_ROUTE"] + (["FUEL_EFFICIENT"] if same_eco else []),
        "travelAdvisory": {"fuelConsumptionMicroliters": "1500000"},
    }]
    if not same_eco:
        routes.append({
            "duration": "1700s", "staticDuration": "1500s", "distanceMeters": 17100,
            "polyline": {"encodedPolyline": eco}, "routeLabels": ["FUEL_EFFICIENT"],
            "travelAdvisory": {"fuelConsumptionMicroliters": "1100000"},
        })
    return {"routes": routes}


def polyline_encode(points):
    """Encoder di riferimento per costruire le fixture."""
    out = []
    prev_lat = prev_lng = 0
    for lat, lng in points:
        ilat, ilng = int(round(lat * 1e5)), int(round(lng * 1e5))
        for delta in (ilat - prev_lat, ilng - prev_lng):
            value = ~(delta << 1) if delta < 0 else delta << 1
            while value >= 0x20:
                out.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            out.append(chr(value + 63))
        prev_lat, prev_lng = ilat, ilng
    return "".join(out)


NOW = datetime(2026, 11, 6, 7, 42, tzinfo=ZoneInfo("Europe/Rome"))


def test_parse_response_with_distinct_eco_route():
    result = routes_api.parse_response(_route_payload(), NOW)
    assert result.fastest.duration_s == 1500
    assert result.fastest.delay_s == 300
    assert result.eco is not None and result.eco.duration_s == 1700


def test_parse_response_with_eco_equal_to_fastest():
    result = routes_api.parse_response(_route_payload(same_eco=True), NOW)
    assert result.eco is None


def test_parse_response_without_routes():
    with pytest.raises(routes_api.RoutesError):
        routes_api.parse_response({}, NOW)


def test_parse_duration_formats():
    assert routes_api.parse_duration("12.6s") == 13
    assert routes_api.parse_duration(None) is None
    with pytest.raises(routes_api.RoutesError):
        routes_api.parse_duration("12m")


def test_card_rows_text_is_ascii_and_complete():
    rows = renderer.card_rows(routes_api.parse_response(_route_payload(), NOW))
    texts = [r.text for r in rows]
    assert texts == ["25 min", "+5 min", "18,4 km", "+3 min -0,4 L", "agg. 07:42"]
    assert rows[1].color == "red"
    assert all(" " <= c <= "~" for t in texts for c in t)


def test_formatting():
    assert renderer.format_duration(3900) == "1 h 05 min"
    assert renderer.format_distance(124_400) == "124 km"


def test_gfx_font_metrics_match_firmware_comment():
    # Layout_common.h: la cap height di FONT_BODY (FreeSans12pt7b) è 17 px.
    assert gfx_font.load("FreeSans12pt7b").cap_height == 17


def test_material_icons_exist():
    for name in ("schedule", "traffic", "straighten", "eco", "update", "trip_origin", "location_on"):
        assert material_icons.mask(name, 22).getbbox() is not None


def _fake_map(viewport: Viewport) -> Image.Image:
    return Image.new("RGB", (viewport.width, viewport.height), (200, 200, 200))


@pytest.mark.parametrize("height,expected", [(290, 44660), (325, 50050)])
def test_arduino_body_length_matches_firmware(height, expected):
    result = routes_api.parse_response(_route_payload(), NOW)
    body = renderer.render_route_image(result, 610, height, "arduino", "bwr", "floyd", _fake_map)
    assert len(body) == expected


def test_static_map_keeps_google_colors():
    """Una sola base per ogni formato: solo lo stile del contenuto, nessun
    colore cambiato, la palette la applica il renderer."""
    viewport = Viewport(43.6, 13.5, 11, 610, 290, 1)
    styles = [v for k, v in static_map.build_params(viewport, "k") if k == "style"]
    assert styles == list(static_map.STYLE_CONTENT)


def test_jpeg_render_and_scale_two():
    result = routes_api.parse_response(_route_payload(), NOW)
    for width, height in [(610, 290), (1220, 580)]:
        body = renderer.render_route_image(result, width, height, "jpg", "rgb", "no", _fake_map)
        image = Image.open(__import__("io").BytesIO(body))
        assert image.size == (width, height)


@pytest.mark.parametrize("colors", ["bw", "bwr", "bwry"])
def test_jpeg_with_colors_stays_in_panel_palette(colors):
    """Con colors il JPEG è l'anteprima del pannello: a meno degli artefatti di
    compressione ogni pixel è un colore della palette, e il rosso c'è solo se la
    palette lo ha."""
    result = routes_api.parse_response(_route_payload(), NOW)
    body = renderer.render_route_image(result, 610, 290, "jpg", colors, "floyd", _fake_map)
    pixels = np.array(Image.open(__import__("io").BytesIO(body)), dtype=np.int16)
    palette = np.array(PALETTES[colors], dtype=np.int16)
    distance = np.abs(pixels[:, :, None, :] - palette[None, None]).sum(-1).min(-1)
    assert distance.mean() < 30
    near_red = (np.abs(pixels - np.array([255, 0, 0])).sum(-1) < 60).any()
    assert near_red == (colors != "bw")


def test_card_area_is_pure_palette():
    """Il riquadro si disegna dopo il dither: dentro non c'è nessun indice di grigio."""
    result = routes_api.parse_response(_route_payload(), NOW)
    body = renderer.render_route_image(result, 610, 290, "arduino", "bwr", "floyd", _fake_map)
    stride = (610 + 7) // 8
    black = np.unpackbits(np.frombuffer(body[:stride * 290], dtype=np.uint8)).reshape(290, stride * 8)[:, :610]
    card_w, card_h = renderer.card_size(renderer.card_rows(result))
    left = 610 - renderer.MARGIN - card_w
    inner = black[renderer.MARGIN + 4:renderer.MARGIN + card_h - 4, left + 4:left + 8]
    # Margine sinistro interno della card, prima delle icone: tutto bianco (bit a 1).
    assert inner.all()


@pytest.mark.parametrize("colors,rgb,plane", [("bwr", (255, 0, 0), 1), ("bwry", (255, 255, 0), 2)])
def test_color_base_reaches_palette_accents(colors, rgb, plane):
    """La base nei colori di Google si converte nella palette completa: un'area
    rossa o gialla della mappa resta rossa o gialla sul pannello."""
    result = routes_api.parse_response(_route_payload(), NOW)
    body = renderer.render_route_image(result, 610, 290, "arduino", colors, "floyd",
                                       lambda v: Image.new("RGB", (v.width, v.height), rgb))
    size = (610 + 7) // 8 * 290
    bits = np.unpackbits(np.frombuffer(body[plane * size:(plane + 1) * size], dtype=np.uint8))
    assert (bits == 0).mean() > 0.5


def test_too_small_image_is_rejected():
    result = routes_api.parse_response(_route_payload(), NOW)
    with pytest.raises(ValueError):
        renderer.render_route_image(result, 120, 80, "jpg", "rgb", "no", _fake_map)


def _atkinson_reference(img_rgb, palette_rgb):
    """Il ciclo Python pixel per pixel da cui deriva dithering._quantize_atkinson:
    la versione per antidiagonali deve restituire gli stessi indici."""
    arr = np.array(img_rgb, dtype=np.float32).tolist()
    h, w = len(arr), len(arr[0])
    palette = [tuple(float(c) for c in rgb) for rgb in palette_rgb]
    result = np.zeros((h, w), dtype=np.uint8)
    for y in range(h):
        for x in range(w):
            p = arr[y][x]
            best_i, best_d = 0, 1e18
            for i, (pr, pg, pb) in enumerate(palette):
                d = (p[0] - pr) * (p[0] - pr) + (p[1] - pg) * (p[1] - pg) + (p[2] - pb) * (p[2] - pb)
                if d < best_d:
                    best_i, best_d = i, d
            result[y, x] = best_i
            e = [(p[c] - palette[best_i][c]) * 0.125 for c in range(3)]
            for dy, dx in ((0, 1), (0, 2), (1, -1), (1, 0), (1, 1), (2, 0)):
                if 0 <= y + dy < h and 0 <= x + dx < w:
                    q = arr[y + dy][x + dx]
                    for c in range(3):
                        q[c] += e[c]
    return result


@pytest.mark.parametrize("size", [(1, 1), (9, 1), (1, 9), (2, 2), (37, 23), (64, 41)])
def test_atkinson_matches_pixel_loop(size):
    rng = np.random.default_rng(size[0] * 100 + size[1])
    image = Image.fromarray(rng.integers(0, 256, (size[1], size[0], 3), dtype=np.uint8))
    for palette in PALETTES.values():
        assert (dithering._quantize_atkinson(image, palette) == _atkinson_reference(image, palette)).all()


def test_example_measures_ram_once_per_size(monkeypatch):
    """La RAM di una cella dipende dalla misura, non dalla mappa: alla seconda
    griglia della stessa misura tracemalloc non riparte."""
    examples._peak_cache.clear()
    starts = []
    real_start = examples.tracemalloc.start
    monkeypatch.setattr(examples.tracemalloc, "start", lambda *a: (starts.append(1), real_start(*a)))
    result = routes_api.parse_response(_route_payload(), NOW)
    for _ in range(2):
        examples.render_examples(result, 1920, 1080, "/route?x", None, _fake_map)
    assert len(starts) == len([c for c in examples.CELLS if isinstance(c, tuple)])
