"""Test degli endpoint con le API Google sostituite da fixture."""

from __future__ import annotations

import io
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import main
import renderer
import routes_api
from tests.test_core import _route_payload


TOKEN = "segreto-di-test"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ROUTEFINDER_TOKEN", TOKEN)
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "chiave-finta")
    calls = {"routes": 0, "gray": []}

    def fake_compute(origin, destination, fuel, api_key):
        calls["routes"] += 1
        return routes_api.parse_response(_route_payload(), datetime(2026, 11, 6, 7, 42, tzinfo=ZoneInfo("Europe/Rome")))

    monkeypatch.setattr(main, "compute_routes", fake_compute)
    def fake_map(viewport, key, gray):
        calls["gray"].append(gray)
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
    assert response.headers["location"] == "/route"


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


def test_jpeg_map_in_color_arduino_in_gray(client):
    headers = {"X-Routefinder-Token": TOKEN}
    client.get(QUERY, headers=headers)
    client.get(QUERY + "&format=arduino", headers=headers)
    assert client.calls["gray"] == [False, True]


def test_jpeg_with_colors_uses_gray_map(client):
    response = client.get(QUERY + "&colors=bwr&dither=atkinson", headers={"X-Routefinder-Token": TOKEN})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert client.calls["gray"] == [True]


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
    assert client.calls == {"routes": 0, "gray": []}
    followed = client.get(QUERY + "&dither=atkinson", headers=headers)
    assert followed.status_code == 200
    assert followed.headers["content-type"] == "image/jpeg"
    assert client.calls["gray"] == [True]
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
