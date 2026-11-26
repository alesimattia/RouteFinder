"""
Picco di memoria del processo nelle richieste più pesanti, sotto il tetto del piano gratuito
di render. Le richieste girano in un processo separato perchè glibc legge
MALLOC_MMAP_THRESHOLD_ solo all'avvio; il test gira solo su Linux, dove l'RSS si misura con
resource, mentre tracemalloc non vede i buffer di Pillow.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# Tetto di 512 MB del piano gratuito di render, con margine per richieste concorrenti.
PEAK_RSS_LIMIT_MB = 400
TOKEN = "segreto-di-test"


def render_env_value(key: str) -> str:
    """Valore di una variabile di envVars in render.yaml, così il test usa quello del deploy."""
    text = (ROOT / "render.yaml").read_text(encoding="utf-8")
    match = re.search(rf"- key: {re.escape(key)}\s*\n\s*value:\s*\"?([^\"\n]+)\"?", text)
    if match is None:
        raise AssertionError(f"{key} non trovata in render.yaml")
    return match.group(1).strip()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RSS e allocatore glibc solo su Linux")
def test_worst_case_peak_rss_fits_render_free_plan():
    env = dict(os.environ,
               MALLOC_MMAP_THRESHOLD_=render_env_value("MALLOC_MMAP_THRESHOLD_"),
               ROUTEFINDER_TOKEN=TOKEN,
               GOOGLE_MAPS_API_KEY="chiave-finta")
    completed = subprocess.run([sys.executable, str(Path(__file__))], cwd=ROOT, env=env,
                               capture_output=True, text=True, timeout=600)
    assert completed.returncode == 0, completed.stderr[-2000:]
    peak_mb = float(completed.stdout.strip().splitlines()[-1])
    assert peak_mb < PEAK_RSS_LIMIT_MB, f"picco RSS {peak_mb:.0f} MB, tetto {PEAK_RSS_LIMIT_MB} MB"


def exercise_worst_cases() -> None:
    """Le richieste più pesanti alla misura massima, con Google sostituito da fixture: la RAM
    dipende da misura e algoritmo, non dal contenuto della mappa."""
    sys.path.insert(0, str(ROOT))
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from fastapi.testclient import TestClient
    from PIL import Image

    import main
    import routes_api
    from tests.test_core import _route_payload

    computed_at = datetime(2026, 11, 6, 7, 42, tzinfo=ZoneInfo("Europe/Rome"))
    main.compute_routes = lambda origin, destination, fuel, api_key: routes_api.parse_response(
        _route_payload(), computed_at)
    main.fetch_base_map = lambda viewport, key: Image.new(
        "RGB", (viewport.width, viewport.height), (230, 230, 230))

    client = TestClient(main.app)
    route = f"/route?origin=43.6158,13.5189&destination=43.5,13.3&width={main.MAX_SIDE}&height={main.MAX_SIDE}"
    urls = [route]
    urls += [f"{route}&format=arduino&colors=bwry&dither={dither}" for dither in ("no", "floyd", "atkinson", "bayer")]
    urls.append(f"/example?width={main.MAX_CANVAS[0]}&height={main.MAX_CANVAS[1]}")
    for url in urls:
        response = client.get(url, headers={"X-Routefinder-Token": TOKEN})
        if response.status_code != 200:
            raise SystemExit(f"{url}: {response.status_code} {response.text[:300]}")


if __name__ == "__main__":
    import resource

    exercise_worst_cases()
    # Su Linux ru_maxrss è in KiB.
    print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
