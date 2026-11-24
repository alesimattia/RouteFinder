"""Test di TTLCache: tetto di voci, voci scadute, singleflight. Orologio e thread sotto controllo."""

import random
import threading
import time
from types import SimpleNamespace

import pytest

import cache as cache_module
from cache import TTLCache


@pytest.fixture
def clock(monkeypatch):
    """Orologio manuale: sostituisce solo il riferimento al modulo time dentro cache.py."""
    now = [0.0]
    monkeypatch.setattr(cache_module, "time", SimpleNamespace(time=lambda: now[0]))
    return now


class Counter:
    """Factory che registra le chiavi calcolate."""

    def __init__(self):
        self.calls = []

    def __call__(self, key):
        return lambda: (self.calls.append(key), key.upper())[1]


def keys(cache):
    return list(cache._store)


def test_without_a_cap_nothing_is_evicted():
    cache = TTLCache(ttl_seconds=3600)
    for i in range(200):
        cache.get_or_compute(i, lambda i=i: i)
    assert len(cache._store) == 200


def test_cap_evicts_the_oldest_entry(clock):
    compute = Counter()
    cache = TTLCache(ttl_seconds=10, max_entries=2)
    for key in "abc":
        cache.get_or_compute(key, compute(key))
    assert keys(cache) == ["b", "c"]
    cache.get_or_compute("b", compute("b"))
    assert compute.calls == ["a", "b", "c"]  # b è ancora in cache
    cache.get_or_compute("a", compute("a"))
    assert compute.calls == ["a", "b", "c", "a"]  # a era stata espulsa e si ricalcola
    assert keys(cache) == ["c", "a"]


def test_reinserting_a_key_moves_it_to_the_end(clock):
    cache = TTLCache(ttl_seconds=10, max_entries=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("a", 3)  # a diventa la più recente
    assert keys(cache) == ["b", "a"]
    cache.set("c", 4)
    assert keys(cache) == ["a", "c"]
    assert cache.get_or_compute("a", None) == 3


def test_expired_entries_leave_before_live_ones(clock):
    """Le voci più vecchie sono anche le prime a scadere: sotto il tetto escono per prime le scadute."""
    cache = TTLCache(ttl_seconds=10, max_entries=2)
    cache.set("a", 1)
    clock[0] = 5
    cache.set("b", 2)
    clock[0] = 11  # a è scaduta, b no
    cache.set("c", 3)
    assert keys(cache) == ["b", "c"]  # b, la più vecchia fra le vive, non viene espulsa


def test_sweep_removes_expired_entries_even_without_a_cap(clock):
    cache = TTLCache(ttl_seconds=1)
    for i in range(70):
        cache.get_or_compute(i, lambda i=i: i)
    clock[0] = 5  # le 70 voci sono scadute
    for i in range(1000, 1064):
        cache.get_or_compute(i, lambda i=i: i)
    assert len(cache._store) == 64
    assert all(key >= 1000 for key in cache._store)


def test_locks_of_removed_keys_are_released(clock):
    cache = TTLCache(ttl_seconds=1, max_entries=3)
    for i in range(200):
        cache.get_or_compute(i, lambda i=i: i)
    assert len(cache._key_locks) < 70  # ben sotto le 200 chiavi viste


def test_invalid_cap_is_rejected():
    with pytest.raises(ValueError):
        TTLCache(60, max_entries=0)
    with pytest.raises(ValueError):
        TTLCache(60, max_entries=-3)


def test_ttl_is_required():
    """Nessun TTL di default: chi crea la cache lo sceglie, e non esiste una variabile d'ambiente che lo cambi."""
    with pytest.raises(TypeError):
        TTLCache()
    with pytest.raises(TypeError):
        TTLCache(max_entries=3)


def test_set_respects_the_cap(clock):
    cache = TTLCache(ttl_seconds=10, max_entries=3)
    for i in range(10):
        cache.set(i, i)
    assert keys(cache) == [7, 8, 9]


def test_hit_does_not_recompute_and_a_none_factory_reads_a_present_entry(clock):
    compute = Counter()
    cache = TTLCache(ttl_seconds=10)
    assert cache.get_or_compute("k", compute("k")) == "K"
    assert cache.get_or_compute("k", compute("k")) == "K"
    assert cache.get_or_compute("k", None) == "K"
    assert compute.calls == ["k"]


def test_expired_entry_is_recomputed(clock):
    compute = Counter()
    cache = TTLCache(ttl_seconds=10)
    cache.get_or_compute("k", compute("k"))
    clock[0] = 10
    cache.get_or_compute("k", compute("k"))
    assert compute.calls == ["k", "k"]


def test_clear_empties_everything(clock):
    cache = TTLCache(ttl_seconds=10, max_entries=3)
    cache.set("a", 1)
    cache.clear()
    assert cache._store == {} and cache._key_locks == {}


def test_singleflight_is_unchanged_by_the_cap():
    cache = TTLCache(ttl_seconds=60, max_entries=2)
    release = threading.Event()
    calls = []
    results = []

    def factory():
        calls.append(1)
        assert release.wait(10)
        return "valore"

    threads = [threading.Thread(target=lambda: results.append(cache.get_or_compute("k", factory)),
                                daemon=True) for _ in range(8)]
    for thread in threads:
        thread.start()
    time.sleep(0.2)  # dà il tempo agli altri thread di mettersi in attesa del primo
    release.set()
    for thread in threads:
        thread.join(10)
    assert len(calls) == 1 and results == ["valore"] * 8


def test_stress_keeps_the_cap_and_never_runs_two_factories_for_one_key():
    cap = 10
    cache = TTLCache(ttl_seconds=3600, max_entries=cap)
    running = set()
    guard = threading.Lock()
    violations = []
    lengths = []

    def factory(key):
        with guard:
            if key in running:
                violations.append(key)
            running.add(key)
        time.sleep(0.0002)
        with guard:
            running.discard(key)
        return key

    def worker(seed):
        rng = random.Random(seed)
        for _ in range(800):
            key = rng.randrange(60)
            assert cache.get_or_compute(key, lambda key=key: factory(key)) == key
            if rng.random() < 0.02:
                with cache._lock:
                    lengths.append(len(cache._store))

    threads = [threading.Thread(target=worker, args=(seed,), daemon=True) for seed in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
        assert not thread.is_alive()
    assert violations == []
    assert lengths and max(lengths) <= cap
    assert len(cache._store) <= cap
