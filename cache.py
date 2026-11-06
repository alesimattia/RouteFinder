"""
Cache in-memory con TTL (thread-safe).

Copia di webapp/cache.py, da tenere allineata. Qui il TTL lo passa main.py,
perchè un percorso col traffico invecchia in minuti e non in ore.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Hashable


DEFAULT_TTL = int(os.environ.get("CACHE_TTL_SECONDS", "7200"))


class TTLCache:
    """
    Cache dict-like con scadenza per-chiave, thread-safe, con singleflight.

    Su cache miss concorrenti con la stessa chiave, solo il primo thread
    esegue la factory; gli altri aspettano il risultato invece di duplicare
    il lavoro. Utile con /debug che genera in parallelo 10 richieste
    dipendenti dallo stesso scraping Movieland: senza singleflight la prima
    apertura a cache fredda farebbe 10 richieste HTTP al sito sorgente.
    """

    # Ogni N get_or_compute con cache miss, sweep di _key_locks per rimuovere
    # i lock le cui chiavi non sono piu' in _store (entry evictate / scadute).
    # Sweep opportunistico: poco frequente per non penalizzare il throughput.
    _KEY_LOCKS_SWEEP_INTERVAL = 64

    def __init__(self, ttl_seconds: int = DEFAULT_TTL) -> None:
        self._ttl = ttl_seconds
        self._store: dict[Hashable, tuple[float, Any]] = {}
        self._lock = threading.Lock()  # protegge _store, _key_locks, _miss_counter
        self._key_locks: dict[Hashable, threading.Lock] = {}
        self._miss_counter = 0

    def _get_key_lock(self, key: Hashable) -> threading.Lock:
        """Ritorna (o crea) un lock dedicato alla chiave."""
        with self._lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._key_locks[key] = lock
            return lock

    def _maybe_sweep_key_locks(self) -> None:
        """
        Pulizia opportunistica di _key_locks: rimuove i lock per chiavi non
        piu' presenti in _store, e che non sono attualmente acquired da altri
        thread. Chiamata ogni _KEY_LOCKS_SWEEP_INTERVAL miss; evita la crescita
        illimitata di _key_locks su long-running.
        """
        with self._lock:
            self._miss_counter += 1
            if self._miss_counter < self._KEY_LOCKS_SWEEP_INTERVAL:
                return
            self._miss_counter = 0
            stale_keys = [k for k in self._key_locks if k not in self._store]
            for k in stale_keys:
                lock = self._key_locks[k]
                # acquire non bloccante: se un altro thread ha il lock in
                # questo istante, salto la rimozione (verra' raccolto al
                # prossimo sweep).
                if lock.acquire(blocking=False):
                    lock.release()
                    del self._key_locks[k]

    def get_or_compute(self, key: Hashable, factory: Callable[[], Any]) -> Any:
        """
        Ritorna il valore cacheato per key; se mancante o scaduto invoca factory.

        Con singleflight: se un altro thread sta gia' computando la stessa
        chiave, aspettiamo il suo risultato invece di duplicare la chiamata.
        """
        # Fast path: cache hit senza bisogno del key-lock.
        now = time.time()
        with self._lock:
            entry = self._store.get(key)
            if entry is not None and (now - entry[0]) < self._ttl:
                return entry[1]

        # Cache miss: serializza per-chiave. Un solo thread entra in factory()
        # per la stessa chiave; gli altri aspettano e poi trovano il valore
        # gia' in cache al double-check.
        key_lock = self._get_key_lock(key)
        with key_lock:
            # Double-check: mentre aspettavamo il lock, qualcun altro potrebbe
            # aver gia' popolato la cache.
            with self._lock:
                entry = self._store.get(key)
                if entry is not None and (time.time() - entry[0]) < self._ttl:
                    return entry[1]

            value = factory()

            with self._lock:
                self._store[key] = (time.time(), value)
            self._maybe_sweep_key_locks()
            return value

    def set(self, key: Hashable, value: Any) -> None:
        """
        Scrive (o sovrascrive) l'entry per `key` con il valore fornito.

        Usato dal flusso force-refresh in main.py: dopo aver computato un valore
        fresco fuori dal percorso get_or_compute, lo si pubblica nella cache per
        i lettori successivi senza ricomputarlo nuovamente.
        """
        with self._lock:
            self._store[key] = (time.time(), value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._key_locks.clear()
