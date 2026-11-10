"""Test degli endpoint con le API Google sostituite da fixture."""

from __future__ import annotations

import io
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import examples
import main
import renderer
import routes_api
from dithering import PALETTES, PLANE_ORDER
from tests.test_core import _route_payload


TOKEN = "segreto-di-test"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ROUTEFINDER_TOKEN", TOKEN)
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "chiave-finta")
    calls = {"routes": 0, "maps": 0}

    def fake_compute(origin, destination, fuel, api_key):
        calls["routes"] += 1
        return routes_api.parse_response(_route_payload(), datetime(2026, 11, 6, 7, 42, tzinfo=ZoneInfo("Europe/Rome")))

    monkeypatch.setattr(main, "compute_routes", fake_compute)
    def fake_map(viewport, key):
        calls["maps"] += 1
        return Image.new("RGB", (viewport.width, viewport.height), (230, 230, 230))

    monkeypatch.setattr(main, "fetch_base_map", fake_map)
    main._route_cache.clear()
    main._image_cache.clear()
    test_client = TestClient(main.app)
    test_client.calls = calls
    return test_client


QUERY = "/route?origin=43.6158,13.5189&destination=43.5,13.3"


def test_health_is_open(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_route_without_token_is_401(client):
    response = client.get(QUERY)
    assert response.status_code == 401
    assert "text/html" not in response.headers.get("content-type", "")


def test_browser_without_token_gets_login_page(client):
    response = client.get(QUERY, headers={"Accept": "text/html"})
    assert response.status_code == 401
    assert "Token di accesso" in response.text
    assert 'value="/route?origin=43.6158,13.5189&amp;destination=43.5,13.3"' in response.text


def test_login_sets_cookie_and_redirects(client):
    response = client.post("/auth", data={"token": TOKEN, "next": QUERY}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == QUERY
    assert "routefinder_token" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]


def test_login_rejects_open_redirect(client):
    response = client.post("/auth", data={"token": TOKEN, "next": "//evil.example/x"}, follow_redirects=False)
    assert response.headers["location"] == "/docs"


def test_login_with_wrong_token(client):
    response = client.post("/auth", data={"token": "sbagliato", "next": QUERY}, follow_redirects=False)
    assert response.status_code == 401


def test_missing_server_token_refuses_everything(client, monkeypatch):
    monkeypatch.delenv("ROUTEFINDER_TOKEN")
    assert client.get(QUERY, headers={"X-Routefinder-Token": ""}).status_code == 401


def test_jpeg_default(client):
    response = client.get(QUERY, headers={"X-Routefinder-Token": TOKEN})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    image = Image.open(io.BytesIO(response.content))
    assert image.mode == "RGB"
    reference = io.BytesIO()
    Image.new("RGB", (8, 8)).save(reference, format="JPEG", quality=renderer.JPEG_QUALITY)
    assert image.quantization == Image.open(reference).quantization


def test_default_size_is_maximum(client):
    response = client.get(QUERY, headers={"X-Routefinder-Token": TOKEN})
    assert Image.open(io.BytesIO(response.content)).size == (main.MAX_SIDE, main.MAX_SIDE)
    only_width = client.get(QUERY + "&width=610", headers={"X-Routefinder-Token": TOKEN})
    assert Image.open(io.BytesIO(only_width.content)).size == (610, main.MAX_SIDE)


def test_jpeg_with_colors_is_panel_preview(client):
    response = client.get(QUERY + "&colors=bwr&dither=atkinson", headers={"X-Routefinder-Token": TOKEN})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert client.calls["maps"] == 1


@pytest.mark.parametrize("colors", ["bw", "bwr", "bwry"])
def test_jpeg_colors_without_dither_defaults_to_threshold(client, colors):
    headers = {"X-Routefinder-Token": TOKEN}
    base = QUERY + f"&width=610&height=290&colors={colors}"
    default = client.get(base, headers=headers).content
    assert default == client.get(base + "&dither=no", headers=headers).content
    assert default != client.get(base + "&dither=floyd", headers=headers).content


@pytest.mark.parametrize("colors", ["bw", "bwr", "bwry"])
def test_arduino_dither_defaults_to_floyd_with_every_palette(client, colors):
    headers = {"X-Routefinder-Token": TOKEN}
    base = QUERY + f"&format=arduino&width=610&height=290&colors={colors}"
    default = client.get(base, headers=headers).content
    assert default == client.get(base + "&dither=floyd", headers=headers).content
    assert default != client.get(base + "&dither=no", headers=headers).content


def test_arduino_without_colors_nor_dither_uses_floyd(client):
    headers = {"X-Routefinder-Token": TOKEN}
    base = QUERY + "&format=arduino&width=610&height=290"
    assert client.get(base, headers=headers).content == \
        client.get(base + "&colors=bwr&dither=floyd", headers=headers).content


def test_jpeg_with_dither_only_redirects_to_default_palette(client):
    headers = {"X-Routefinder-Token": TOKEN}
    redirect = client.get(QUERY + "&dither=atkinson", headers=headers, follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"] == QUERY + "&dither=atkinson&colors=" + main.DEFAULT_COLORS
    # Il redirect precede ogni chiamata a pagamento.
    assert client.calls == {"routes": 0, "maps": 0}
    followed = client.get(QUERY + "&dither=atkinson", headers=headers)
    assert followed.status_code == 200
    assert followed.headers["content-type"] == "image/jpeg"
    assert client.calls["maps"] == 1
    explicit = client.get(QUERY + "&colors=bwr&dither=atkinson", headers=headers)
    assert followed.content == explicit.content


def test_arduino_exact_length_and_no_gzip(client):
    response = client.get(QUERY + "&format=arduino&colors=bwr&dither=floyd&width=610&height=290",
                          headers={"X-Routefinder-Token": TOKEN, "Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers["content-length"] == "44660"
    assert "content-encoding" not in response.headers


def test_route_cache_avoids_second_google_call(client):
    headers = {"X-Routefinder-Token": TOKEN}
    client.get(QUERY, headers=headers)
    client.get(QUERY + "&format=arduino", headers=headers)
    assert client.calls["routes"] == 1


@pytest.mark.parametrize("query", [
    "/route?origin=43.6&destination=43.5,13.3",
    "/route?origin=95,13&destination=43.5,13.3",
    "/route?origin=43.6,13.5&destination=43.5,13.3&width=2000",
    "/route?origin=43.6,13.5&destination=43.5,13.3&fuel=lpg",
])
def test_bad_parameters(client, query):
    response = client.get(query, headers={"X-Routefinder-Token": TOKEN})
    assert response.status_code in (400, 422)


def test_root_redirects_to_docs(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/docs"


def test_example_requires_token(client):
    assert client.get("/example").status_code == 401


EXAMPLE = "/example"


def test_example_browser_gets_viewport_page_without_google_calls(client):
    response = client.get(EXAMPLE, headers={"X-Routefinder-Token": TOKEN, "Accept": "text/html"})
    assert response.status_code == 200
    assert "window.innerWidth" in response.text and "window.innerHeight" in response.text
    assert '(location.search ? "&" : "?")' in response.text
    assert client.calls == {"routes": 0, "maps": 0}


def test_example_calls_each_google_api_once_and_caches(client):
    headers = {"X-Routefinder-Token": TOKEN}
    response = client.get(EXAMPLE, headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert "content-encoding" not in response.headers
    assert Image.open(io.BytesIO(response.content)).size == examples.DEFAULT_CANVAS
    assert client.calls == {"routes": 1, "maps": 1}
    assert client.get(EXAMPLE, headers=headers).content == response.content
    assert client.calls == {"routes": 1, "maps": 1}
    # La querystring non sceglie il percorso: origin e destination sono fissi.
    assert client.get(EXAMPLE + "?origin=1,1&destination=2,2", headers=headers).content == response.content


@pytest.mark.parametrize("size", [(1512, 860), (1440, 790), (2560, 1300)])
def test_example_fills_the_viewport(client, size):
    response = client.get(EXAMPLE + f"?width={size[0]}&height={size[1]}", headers={"X-Routefinder-Token": TOKEN})
    assert response.status_code == 200
    assert Image.open(io.BytesIO(response.content)).size == size


@pytest.mark.parametrize("size", [(800, 600), (3000, 1300)])
def test_example_rejects_canvas_too_small_or_too_large(client, size):
    response = client.get(EXAMPLE + f"?width={size[0]}&height={size[1]}", headers={"X-Routefinder-Token": TOKEN})
    assert response.status_code == 400


def _decode_planes(body: bytes, colors: str, width: int, height: int) -> np.ndarray:
    """Piani di format=arduino riportati a RGB, per confrontarli con la griglia."""
    stride = (width + 7) // 8
    rgb = np.full((height, width, 3), 255, dtype=np.uint8)
    for index, channel in enumerate(PLANE_ORDER[colors]):
        plane = np.frombuffer(body[index * stride * height:(index + 1) * stride * height], dtype=np.uint8)
        bits = np.unpackbits(plane).reshape(height, stride * 8)[:, :width]
        rgb[bits == 0] = PALETTES[colors][index + 1]
    return rgb


def test_example_cells_match_route(client):
    """Ogni cella del pannello è identica, pixel per pixel, ai piani che /route
    restituisce con gli stessi parametri e la misura della cella."""
    headers = {"X-Routefinder-Token": TOKEN}
    canvas = np.array(Image.open(io.BytesIO(client.get(EXAMPLE, headers=headers).content)).convert("RGB"))
    origin = main.parse_point(examples.ORIGIN, "origin")
    destination = main.parse_point(examples.DESTINATION, "destination")
    result = main._route_cache.get_or_compute((origin, destination, "gasoline"), None)
    grid = examples.grid_for(result, *examples.DEFAULT_CANVAS)
    w, h = grid.cell_w, grid.cell_h
    for index, cell in enumerate(examples.CELLS):
        if not isinstance(cell, tuple) or cell[0] == "rgb":
            continue
        colors, dither = cell
        body = client.get(f"/route?origin={examples.ORIGIN}&destination={examples.DESTINATION}"
                          f"&format=arduino&colors={colors}&dither={dither}&width={w}&height={h}",
                          headers=headers).content
        x, y = grid.origin(index)
        assert (canvas[y:y + h, x:x + w] == _decode_planes(body, colors, w, h)).all(), cell


def _access_record(client_addr, path, status, method="GET"):
    """Riga del log di accesso con gli stessi args di uvicorn 0.54 (h11 e httptools)."""
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 0, '%s - "%s %s HTTP/%s" %d',
                             (client_addr, method, path, "1.1", status), None)


@pytest.mark.parametrize("client_addr, path, status, kept", [
    ("10.231.26.208:44150", "/health", 200, False),   # health check interno di render
    ("5.61.222.202:0", "/health", 200, True),         # pre-warm del firmware o browser, via proxy
    ("127.0.0.1:51000", "/health", 200, True),        # curl in locale
    ("10.231.26.208:44150", "/health", 503, True),    # health check fallito
    ("10.231.26.208:44150", "/docs", 200, True),      # altro path dalla rete interna
    ("10.231.26.208:44150", "/health?x=1", 200, True),
    ("34.83.22.237:0", "/", 307, True),               # probe di deploy di render
    ("", "/health", 200, True),                       # client sconosciuto
])
def test_render_health_check_filter(client_addr, path, status, kept):
    assert main.RenderHealthCheckFilter().filter(_access_record(client_addr, path, status)) is kept


def test_render_health_check_filter_is_installed():
    assert any(isinstance(f, main.RenderHealthCheckFilter) for f in logging.getLogger("uvicorn.access").filters)
