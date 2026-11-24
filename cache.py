"""
Cache in-memory con TTL e tetto di voci (thread-safe).

Copia di webapp/cache.py, da tenere allineata. Qui il TTL lo passa main.py,
perchè un percorso col traffico invecchia in minuti e non in ore.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Hashable, Optional


class TTLCache:
    """
    Cache dict-like con scadenza per-chiave e tetto di voci, thread-safe, con singleflight.

    Su cache miss concorrenti con la stessa chiave, solo il primo thread
    esegue la factory; gli altri aspettano il risultato invece di duplicare
    il lavoro. Utile quando più richieste dipendono dalla stessa operazione
    costosa: senza singleflight ognuna la ripeterebbe a cache fredda.

    Con max_entries la cache non cresce oltre il tetto: a ogni inserimento sopra il
    tetto toglie le voci più vecchie, che sono anche le più vicine a scadere. Le voci
    scadute escono anche dallo sweep periodico, quindi nemmeno una cache senza tetto
    trattiene chiavi morte.
    """

    # Ogni N get_or_compute con cache miss, sweep di _store (voci scadute) e di
    # _key_locks (lock di chiavi non piu' in _store). Opportunistico: poco
    # frequente per non penalizzare il throughput.
    _KEY_LOCKS_SWEEP_INTERVAL = 64

    def __init__(self, ttl_seconds: int, max_entries: Optional[int] = None) -> None:
        if max_entries is not None and max_entries < 1:
            raise ValueError("max_entries deve essere almeno 1")
        self._ttl = ttl_seconds
        self._max_entries = max_entries
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

    def _purge_expired_locked(self, now: float) -> None:
        """Toglie le voci scadute a `now`; da chiamare con _lock tenuto."""
        expired = [key for key, (stored_at, _) in self._store.items()
                   if now - stored_at >= self._ttl]
        for key in expired:
            del self._store[key]

    def _store_locked(self, key: Hashable, value: Any) -> None:
        """
        Inserisce o sostituisce una voce e rispetta il tetto; da chiamare con _lock tenuto.

        Reinserire una chiave la sposta in coda: l'ordine dei dict coincide con quello
        di scadenza, quindi le voci più vecchie sono anche le prime a scadere e togliere
        quelle basta a lasciare fuori per prime le scadute.
        """
        self._store.pop(key, None)
        self._store[key] = (time.time(), value)
        if self._max_entries is not None:
            while len(self._store) > self._max_entries:
                del self._store[next(iter(self._store))]

    def _maybe_sweep_key_locks(self) -> None:
        """
        Pulizia opportunistica, ogni _KEY_LOCKS_SWEEP_INTERVAL miss: toglie le voci
        scadute di _store e poi i lock per chiavi non piu' presenti in _store che non
        sono attualmente acquired da altri thread. Evita la crescita illimitata di
        _store e di _key_locks su long-running.
        """
        with self._lock:
            self._miss_counter += 1
            if self._miss_counter < self._KEY_LOCKS_SWEEP_INTERVAL:
                return
            self._miss_counter = 0
            self._purge_expired_locked(time.time())
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
            # Double-check: mentre aspettavamo il lock, qualcuno altro potrebbe
            # aver gia' popolato la cache.
            with self._lock:
                entry = self._store.get(key)
                if entry is not None and (time.time() - entry[0]) < self._ttl:
                    return entry[1]

            value = factory()

            with self._lock:
                self._store_locked(key, value)
            self._maybe_sweep_key_locks()
            return value

    def set(self, key: Hashable, value: Any) -> None:
        """
        Scrive (o sovrascrive) l'entry per `key` con il valore fornito.

        Pubblica nella cache un valore calcolato fuori dal percorso
        get_or_compute, per i lettori successivi, senza ricomputarlo.
        """
        with self._lock:
            self._store_locked(key, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._key_locks.clear()
