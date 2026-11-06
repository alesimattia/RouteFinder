"""Test degli endpoint con le API Google sostituite da fixture."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import main
import routes_api
from tests.test_core import _route_payload


TOKEN = "segreto-di-test"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ROUTEFINDER_TOKEN", TOKEN)
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "chiave-finta")
    calls = {"routes": 0}

    def fake_compute(origin, destination, fuel, api_key):
        calls["routes"] += 1
        return routes_api.parse_response(_route_payload(), datetime(2026, 11, 6, 7, 42, tzinfo=ZoneInfo("Europe/Rome")))

    monkeypatch.setattr(main, "compute_routes", fake_compute)
    monkeypatch.setattr(main, "fetch_base_map",
                        lambda viewport, key: Image.new("RGB", (viewport.width, viewport.height), (230, 230, 230)))
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
    assert "X-Routefinder-Warning" not in response.headers


def test_jpeg_ignores_dither_with_warning(client):
    response = client.get(QUERY + "&dither=floyd", headers={"X-Routefinder-Token": TOKEN})
    assert response.headers["X-Routefinder-Warning"]


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
