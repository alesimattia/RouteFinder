"""I moduli del servizio stanno nella root del repository, non in un pacchetto."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def empty_viewport_cache():
    """La viewport ricordata da un test non deve condizionare il fit del successivo."""
    import renderer

    renderer._viewport_cache.clear()
    yield
    renderer._viewport_cache.clear()
