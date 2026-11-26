"""Test della logica pura: polilinea, proiezione e fit, parsing della Routes API,
formato arduino e testo del riquadro."""

from __future__ import annotations

import json
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


@pytest.mark.parametrize("scale,width,height", [(1, 600, 300), (1, 640, 480), (2, 1200, 600)])
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
    viewport = fit_viewport([path], [], 600, 300, [], margin=6, padding=6)
    assert viewport.zoom >= 16
    tighter = fit_viewport([path], [], 600, 300, [], margin=6, padding=6, max_zoom=viewport.zoom + 1)
    assert tighter.zoom == viewport.zoom


def test_fit_accounts_for_marker_extent():
    path = [(43.60, 13.50), (43.60, 13.50)]
    marker = Marker(43.60, 13.50, -13, -28, 13, 2)
    viewport = fit_viewport([path], [marker], 600, 300, [], margin=6, padding=0)
    x, y = viewport.project(43.60, 13.50)
    assert y - 28 >= 6 - 1


def _shifted(viewport: Viewport, dx_px: int, dy_px: int, zoom: int | None = None) -> Viewport:
    """Viewport con il centro spostato di pixel interi (allo zoom indicato, se dato)."""
    zoom = viewport.zoom if zoom is None else zoom
    k = 2 ** zoom * viewport.scale
    cx, cy = world_xy(viewport.center_lat, viewport.center_lng)
    lat, lng = world_to_latlng(cx + dx_px / k, cy + dy_px / k)
    return Viewport(round(lat, 6), round(lng, 6), zoom, viewport.width, viewport.height, viewport.scale)


@pytest.mark.parametrize("scale,width,height", [(1, 600, 300), (1, 640, 480), (2, 1200, 600)])
def test_fit_returns_admissible_prefer_unchanged(scale, width, height):
    path = _diagonal()
    card = Rect(width - 200 * scale, 6 * scale, width - 6 * scale, 140 * scale)
    logo = Rect(0, height - 28 * scale, 72 * scale, height - 1)
    args = ([path], [], width, height, [card, logo])
    kwargs = dict(margin=6 * scale, padding=6 * scale, scale=scale)
    first = fit_viewport(*args, **kwargs)
    assert fit_viewport(*args, **kwargs, prefer=first) == first


def test_fit_keeps_prefer_that_the_plain_search_would_not_choose():
    """Con uno zoom basso il tracciato è un punto: ogni traslazione ammissibile vale, e
    `prefer` vince anche se la ricerca normale sceglierebbe un'altra viewport."""
    path = _diagonal(start=(43.600, 13.500), end=(43.602, 13.503))
    kwargs = dict(margin=6, padding=6, max_zoom=14)
    plain = fit_viewport([path], [], 600, 300, [], **kwargs)
    assert plain.zoom == 14
    for prefer in (_shifted(plain, 40, -25), _shifted(plain, -90, 30, zoom=13)):
        kept = fit_viewport([path], [], 600, 300, [], **kwargs, prefer=prefer)
        assert kept == prefer and kept != plain


def test_fit_recomputes_when_prefer_is_not_admissible():
    path = _diagonal(start=(43.600, 13.500), end=(43.602, 13.503))
    kwargs = dict(margin=6, padding=6, max_zoom=14)
    plain = fit_viewport([path], [], 600, 300, [], **kwargs)
    # Il tracciato uscirebbe dall'immagine.
    assert fit_viewport([path], [], 600, 300, [], **kwargs, prefer=_shifted(plain, 450, 0)) == plain
    # Il tracciato cadrebbe nella zona vietata, che coincide con il centro dell'immagine.
    forbidden = [Rect(250, 100, 350, 200)]
    blocked = fit_viewport([path], [], 600, 300, forbidden, **kwargs)
    assert blocked != plain
    assert fit_viewport([path], [], 600, 300, forbidden, **kwargs, prefer=plain) == blocked
    # Una prefer di un'altra misura non si usa.
    other_size = Viewport(plain.center_lat, plain.center_lng, plain.zoom, 640, 300, 1)
    assert fit_viewport([path], [], 600, 300, [], **kwargs, prefer=_shifted(other_size, 40, 0)) == plain


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


class _FakeResponse:
    """Risposta HTTP 200 di Google con un corpo qualsiasi, per compute_routes()."""

    status_code = 200

    def __init__(self, body: str):
        self.text = body

    def json(self):
        return json.loads(self.text)


class _FakeSession:
    """Sessione che restituisce sempre la stessa risposta, come requests.Session.post."""

    def __init__(self, response):
        self._response = response

    def post(self, *args, **kwargs):
        return self._response


INVALID_ROUTES_BODIES = [
    "<html>gateway</html>",
    "[]",
    '{"routes": "x"}',
    '{"routes": [{"duration": "10s", "polyline": "oops"}]}',
    '{"routes": [{"duration": "10s", "distanceMeters": "abc", "polyline": {"encodedPolyline": "_p~iF~ps|U"}}]}',
    '{"routes": [{"duration": "infs", "polyline": {"encodedPolyline": "_p~iF~ps|U"}}]}',
]


@pytest.mark.parametrize("body", INVALID_ROUTES_BODIES)
def test_compute_routes_turns_an_invalid_200_body_into_routes_error(body):
    session = _FakeSession(_FakeResponse(body))
    with pytest.raises(routes_api.RoutesError):
        routes_api.compute_routes((43.6, 13.5), (43.5, 13.3), "gasoline", "chiave", session=session)


def test_compute_routes_parses_a_valid_200_body():
    session = _FakeSession(_FakeResponse(json.dumps(_route_payload())))
    result = routes_api.compute_routes((43.6, 13.5), (43.5, 13.3), "gasoline", "chiave", session=session)
    assert result.fastest.duration_s == 1500


def test_card_rows_text_is_ascii_and_complete():
    rows = renderer.card_rows(routes_api.parse_response(_route_payload(), NOW))
    texts = [r.text for r in rows]
    assert texts == ["25 min", "+5 min", "18,4 km", "+3 min -0,4 L", "agg. 07:42"]
    assert rows[1].color == "red"
    assert all(" " <= c <= "~" for t in texts for c in t)


def _card_width(duration_s=1500, with_delay=True, with_eco=True, computed_at=NOW, stable=True) -> int:
    """Larghezza a scale=1 del riquadro di un percorso con durata e righe date."""
    payload = _route_payload(same_eco=not with_eco)
    fastest = payload["routes"][0]
    fastest["duration"] = f"{duration_s}s"
    if not with_delay:
        del fastest["staticDuration"]
    result = routes_api.parse_response(payload, computed_at)
    assert (result.fastest.delay_s is not None) == with_delay and (result.eco is not None) == with_eco
    return renderer.card_size(renderer.card_rows(result), stable=stable)[0]


def test_stable_card_width_is_fixed_for_routes_under_one_hour():
    """Con le avanzate dei glifi e i testi di riserva la larghezza non dipende da
    durata ("35 min", "41 min"), ora di calcolo, riga ritardo o riga eco: 187 px a scale=1."""
    times = (NOW, NOW.replace(hour=11, minute=59), NOW.replace(hour=7, minute=15))
    widths = {_card_width(duration_s, with_delay, with_eco, computed_at)
              for duration_s in (2100, 2460, 600)
              for with_delay in (True, False)
              for with_eco in (True, False)
              for computed_at in times}
    assert widths == {187}


def test_stable_card_width_grows_for_text_wider_than_the_reserve():
    """Un tragitto di un'ora o più ("1 h 05 min") è più largo della riserva "59 min"."""
    assert _card_width(duration_s=3900) > 187


def test_card_width_without_stable_follows_the_real_text():
    """Senza `stable` (/example) il riquadro è largo quanto l'inchiostro del testo reale."""
    assert _card_width(stable=False) == 170
    assert _card_width(with_eco=False, stable=False) == 137


def test_card_reserve_texts_fit_inside_their_row_advance():
    """L'inchiostro di ogni testo, reale o di riserva, sta dentro l'avanzata che dimensiona
    il riquadro, quindi il padding destro non viene mai eroso."""
    rows = renderer.card_rows(routes_api.parse_response(_route_payload(), NOW))
    samples = [(kind.font, kind.reserve_text) for kind in renderer.CARD_ROW_KINDS]
    samples += [(row.font, row.text) for row in rows]
    for font_name, text in samples:
        font = gfx_font.load(font_name)
        x1, _, w, _ = font.text_bounds(text)
        assert x1 + w <= font.advance(text), (font_name, text)


def test_draw_card_has_the_measured_size():
    result = routes_api.parse_response(_route_payload(), NOW)
    rows = renderer.card_rows(result)
    _, colors = renderer._color_table("bwr")
    assert renderer.draw_card(rows, colors, "P").size == renderer.card_size(rows)
    stable_size = renderer.card_size(rows, stable=True)
    assert renderer.draw_card(rows, colors, "P", stable_size[0]).size == stable_size


def _noisy_map(viewport: Viewport) -> Image.Image:
    """Base di rumore che dipende dalla viewport: se il fit si sposta, il dither cambia ovunque."""
    seed = abs(round(viewport.center_lat * 1e6)) * 31 + abs(round(viewport.center_lng * 1e6)) + viewport.zoom
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 256, (viewport.height, viewport.width, 3), dtype=np.uint8))


def test_frames_of_the_same_route_differ_only_inside_the_card():
    """Con la chiave di /route, durata e ora diverse non spostano il bordo del riquadro
    nè il fit: fuori dal riquadro i piani sono identici bit per bit, anche con una base
    che il dither riempie di punti neri e rossi. Le chiavi sono distinte perchè la
    viewport ricordata non mascheri la larghezza fissa."""
    width, height = 600, 300
    renders = []
    for index, (duration_s, computed_at) in enumerate(((1500, NOW.replace(hour=7, minute=15)),
                                                       (1000, NOW.replace(hour=7, minute=20)))):
        payload = _route_payload()
        payload["routes"][0]["duration"] = f"{duration_s}s"
        result = routes_api.parse_response(payload, computed_at)
        body = renderer.render_route_image(result, width, height, "arduino", "bwr", "floyd", _noisy_map,
                                           route_key=("frame", index))
        stride = (width + 7) // 8
        planes = [np.unpackbits(np.frombuffer(body[i * stride * height:(i + 1) * stride * height], dtype=np.uint8))
                  .reshape(height, stride * 8)[:, :width] for i in range(2)]
        renders.append((result, planes))
    card_w, card_h = renderer.card_size(renderer.card_rows(renders[0][0]), stable=True)
    outside = np.ones((height, width), dtype=bool)
    outside[:renderer.MARGIN + card_h + 1, width - renderer.MARGIN - card_w - 1:] = False
    for plane_a, plane_b in zip(renders[0][1], renders[1][1]):
        assert (plane_a[outside] == plane_b[outside]).all()
    assert any((a != b).any() for a, b in zip(renders[0][1], renders[1][1]))


def test_route_layout_reuses_the_viewport_of_the_same_route_and_size(monkeypatch):
    result = routes_api.parse_response(_route_payload(), NOW)
    preferred = []
    real_fit = renderer.fit_viewport

    def recording_fit(*args, **kwargs):
        preferred.append(kwargs.get("prefer"))
        return real_fit(*args, **kwargs)

    monkeypatch.setattr(renderer, "fit_viewport", recording_fit)
    key = ((43.6158, 13.5189), (43.5, 13.3), "gasoline")
    first = renderer.route_layout(result, 600, 300, key).viewport
    second = renderer.route_layout(result, 600, 300, key).viewport
    assert preferred == [None, first] and second == first
    # Un'altra misura, un altro percorso o nessuna chiave non riusano la viewport.
    renderer.route_layout(result, 640, 480, key)
    renderer.route_layout(result, 600, 300, ((1.0, 2.0), (3.0, 4.0), "gasoline"))
    renderer.route_layout(result, 600, 300)
    assert preferred[2:] == [None, None, None]


def test_route_layout_keeps_the_remembered_viewport_when_the_card_shrinks():
    """Senza la rotta eco il riquadro è più basso e il tracciato un sottoinsieme del
    precedente: la viewport ricordata resta ammissibile e non si sposta, mentre un fit
    da zero la porterebbe altrove."""
    key = ((43.6158, 13.5189), (43.5, 13.3), "gasoline")
    with_eco = routes_api.parse_response(_route_payload(), NOW)
    without_eco = routes_api.parse_response(_route_payload(same_eco=True), NOW)
    first = renderer.route_layout(with_eco, 600, 300, key).viewport
    fresh = renderer.route_layout(without_eco, 600, 300).viewport
    assert fresh != first
    assert renderer.route_layout(without_eco, 600, 300, key).viewport == first


def test_formatting():
    assert renderer.format_duration(3900) == "1 h 05 min"
    assert renderer.format_distance(124_400) == "124 km"


def test_gfx_font_cap_height_is_y_offset_of_capital_h():
    # La cima della "H" di FreeSans12pt7b sta 17 px sopra la baseline (yOffset = -17).
    assert gfx_font.load("FreeSans12pt7b").cap_height == 17


def test_material_icons_exist():
    for name in ("schedule", "traffic", "straighten", "eco", "update", "trip_origin", "location_on"):
        assert material_icons.mask(name, 22).getbbox() is not None


def _fake_map(viewport: Viewport) -> Image.Image:
    return Image.new("RGB", (viewport.width, viewport.height), (200, 200, 200))


@pytest.mark.parametrize("width,height,colors,expected", [
    (400, 300, "bwr", 2 * 50 * 300),
    (301, 200, "bwry", 3 * 38 * 200),
    (320, 240, "bw", 1 * 40 * 240),
])
def test_arduino_body_length_follows_plane_formula(width, height, colors, expected):
    """Content-Length = piani * ceil(width/8) * height, anche con larghezza non multipla di 8."""
    result = routes_api.parse_response(_route_payload(), NOW)
    body = renderer.render_route_image(result, width, height, "arduino", colors, "floyd", _fake_map)
    assert len(body) == expected


def test_static_map_keeps_google_colors():
    """Una sola base per ogni formato: solo lo stile del contenuto, nessun
    colore cambiato, la palette la applica il renderer."""
    viewport = Viewport(43.6, 13.5, 11, 600, 300, 1)
    styles = [v for k, v in static_map.build_params(viewport, "k") if k == "style"]
    assert styles == list(static_map.STYLE_CONTENT)


def test_jpeg_render_and_scale_two():
    result = routes_api.parse_response(_route_payload(), NOW)
    for width, height in [(600, 300), (1200, 600)]:
        body = renderer.render_route_image(result, width, height, "jpg", "rgb", "no", _fake_map)
        image = Image.open(__import__("io").BytesIO(body))
        assert image.size == (width, height)


@pytest.mark.parametrize("colors", ["bw", "bwr", "bwry"])
def test_jpeg_with_colors_stays_in_palette(colors):
    """Con colors il JPEG è l'anteprima e-paper: a meno degli artefatti di
    compressione ogni pixel è un colore della palette, e il rosso c'è solo se la
    palette lo ha."""
    result = routes_api.parse_response(_route_payload(), NOW)
    body = renderer.render_route_image(result, 600, 300, "jpg", colors, "floyd", _fake_map)
    pixels = np.array(Image.open(__import__("io").BytesIO(body)), dtype=np.int16)
    palette = np.array(PALETTES[colors], dtype=np.int16)
    distance = np.abs(pixels[:, :, None, :] - palette[None, None]).sum(-1).min(-1)
    assert distance.mean() < 30
    near_red = (np.abs(pixels - np.array([255, 0, 0])).sum(-1) < 60).any()
    assert near_red == (colors != "bw")


def test_card_area_is_pure_palette():
    """Il riquadro si disegna dopo il dither: dentro non c'è nessun indice di grigio."""
    result = routes_api.parse_response(_route_payload(), NOW)
    body = renderer.render_route_image(result, 600, 300, "arduino", "bwr", "floyd", _fake_map)
    stride = (600 + 7) // 8
    black = np.unpackbits(np.frombuffer(body[:stride * 300], dtype=np.uint8)).reshape(300, stride * 8)[:, :600]
    card_w, card_h = renderer.card_size(renderer.card_rows(result))
    left = 600 - renderer.MARGIN - card_w
    inner = black[renderer.MARGIN + 4:renderer.MARGIN + card_h - 4, left + 4:left + 8]
    # Margine sinistro interno della card, prima delle icone: tutto bianco (bit a 1).
    assert inner.all()


@pytest.mark.parametrize("colors,rgb,plane", [("bwr", (255, 0, 0), 1), ("bwry", (255, 255, 0), 2)])
def test_color_base_reaches_palette_accents(colors, rgb, plane):
    """La base nei colori di Google si converte nella palette completa: un'area
    rossa o gialla della mappa resta rossa o gialla nei piani e-paper."""
    result = routes_api.parse_response(_route_payload(), NOW)
    body = renderer.render_route_image(result, 600, 300, "arduino", colors, "floyd",
                                       lambda v: Image.new("RGB", (v.width, v.height), rgb))
    size = (600 + 7) // 8 * 300
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


def _bayer_reference(img_rgb, palette_rgb):
    """Bayer sull'immagine intera in un solo passaggio: la versione a blocchi deve
    restituire gli stessi indici."""
    arr = np.array(img_rgb, dtype=np.float32)
    h, w, _ = arr.shape
    norm = (dithering.BAYER_8X8 + 0.5) / 64.0 - 0.5
    tiled = np.tile(norm, ((h + 7) // 8, (w + 7) // 8))[:h, :w]
    flat = (arr + tiled[:, :, None] * 64.0).reshape(-1, 3)
    palette = np.array(palette_rgb, dtype=np.float32)
    diffs = flat[:, None, :] - palette[None, :, :]
    return np.argmin(np.sum(diffs * diffs, axis=2), axis=1).astype(np.uint8).reshape(h, w)


@pytest.mark.parametrize("size", [(1, 1), (9, 1), (1, 9), (13, 29), (64, 41)])
def test_bayer_chunks_match_whole_image(size, monkeypatch):
    # Blocchi di poche righe, che non sono multipli di 8, per attraversare i bordi dei blocchi.
    monkeypatch.setattr(dithering, "_BAYER_CHUNK_PIXELS", 40)
    rng = np.random.default_rng(size[0] * 100 + size[1])
    image = Image.fromarray(rng.integers(0, 256, (size[1], size[0], 3), dtype=np.uint8))
    for palette in PALETTES.values():
        assert (dithering._quantize_bayer(image, palette) == _bayer_reference(image, palette)).all()


def _pack_reference(mask):
    """Impacchettamento bit per bit, MSB-first e righe completate con bit a 1: il
    formato che i client embedded decodificano."""
    h, w = mask.shape
    stride = (w + 7) // 8
    packed = bytearray(h * stride)
    for y in range(h):
        for x in range(stride * 8):
            if x >= w or mask[y, x]:
                packed[y * stride + x // 8] |= 0x80 >> (x % 8)
    return bytes(packed)


@pytest.mark.parametrize("width", [1, 7, 8, 9, 16, 61])
def test_pack_mask_msb_matches_bit_loop(width):
    mask = np.random.default_rng(width).random((5, width)) < 0.5
    assert dithering.pack_mask_msb(mask) == _pack_reference(mask)


@pytest.mark.parametrize("mode", ["L", "RGBA", "P"])
def test_quantize_converts_other_modes_to_rgb(mode):
    rgb = Image.fromarray(np.random.default_rng(3).integers(0, 256, (23, 37, 3), dtype=np.uint8))
    other = rgb.convert(mode)
    for dither in ("no", "floyd", "atkinson", "bayer"):
        expected = dithering.quantize(other.convert("RGB"), "bwry", dither)
        assert (dithering.quantize(other, "bwry", dither) == expected).all()


def test_quantize_leaves_rgb_input_untouched():
    # quantize() passa agli algoritmi l'immagine del chiamante senza copiarla.
    image = Image.fromarray(np.random.default_rng(5).integers(0, 256, (23, 37, 3), dtype=np.uint8))
    before = image.tobytes()
    for dither in ("no", "floyd", "atkinson", "bayer"):
        dithering.quantize(image, "bwry", dither)
    assert image.tobytes() == before


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


def test_example_threshold_at_16_9_follows_the_real_text_width():
    """/example non usa il riquadro a larghezza fissa: a 16:9 con il riquadro completo
    (eco e ritardo, 170 px di testo reale) la soglia è 1223x688."""
    result = routes_api.parse_response(_route_payload(), NOW)
    for canvas in [(1223, 688), (1225, 689), (1280, 720)]:
        examples.grid_for(result, *canvas)
    for canvas in [(1222, 687), (1200, 675)]:
        with pytest.raises(ValueError):
            examples.grid_for(result, *canvas)
    # Senza la riga eco il riquadro è più stretto e la soglia scende.
    examples.grid_for(routes_api.parse_response(_route_payload(same_eco=True), NOW), 1200, 675)


@pytest.mark.parametrize("canvas,landscape", [((1920, 1200), True), ((1100, 1000), True), ((900, 1400), False)])
def test_example_grid_cells_resemble_the_canvas(canvas, landscape):
    """Le celle hanno l'orientazione della tela: larghe in una finestra orizzontale,
    alte in una verticale, senza proporzioni fisse imposte dal codice."""
    result = routes_api.parse_response(_route_payload(), NOW)
    grid = examples.grid_for(result, *canvas)
    assert (grid.cell_w > grid.cell_h) == landscape
