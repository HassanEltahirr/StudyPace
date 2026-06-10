from __future__ import annotations

import threading
import time
from collections.abc import Hashable

from database import current_request_username

_CACHE: dict[tuple[Hashable, ...], tuple[float, object]] = {}
_LOCK = threading.Lock()


def workspace_cache_key(namespace: str, *parts: Hashable) -> tuple[Hashable, ...]:
    return (namespace, current_request_username() or "public", *parts)


def get_cached(key: tuple[Hashable, ...]):
    now = time.time()
    with _LOCK:
        item = _CACHE.get(key)
        if not item:
            return None
        expires_at, value = item
        if expires_at <= now:
            _CACHE.pop(key, None)
            return None
        return value


def set_cached(key: tuple[Hashable, ...], value, ttl_seconds: int) -> None:
    if ttl_seconds <= 0:
        return
    with _LOCK:
        _CACHE[key] = (time.time() + ttl_seconds, value)


def clear_workspace_cache(username: str | None = None) -> None:
    workspace = (username or current_request_username() or "public").strip().lower() or "public"
    with _LOCK:
        for key in list(_CACHE):
            if len(key) > 1 and key[1] == workspace:
                _CACHE.pop(key, None)
