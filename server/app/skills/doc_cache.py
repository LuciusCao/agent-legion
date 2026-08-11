"""TTL memoization for the DB-backed skill source/lock documents.

Agent dispatch calls ``SkillManager.get_skill_dir`` several times per second;
reading the ``skill_sources`` / ``skill_lock`` documents from Postgres on every
call is wasted work because they change only through admin operations
(skill-sources PUT, relock, CLI ``skills-lock``). Those writes go through other
SkillManager instances or processes, so invalidation here is time-based only:
a stale read delays visibility of an admin change by at most the TTL — the
same class of race that already exists between the separate source-update and
relock calls.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any


class SkillDocCache:
    """Thread-safe TTL cache for a handful of small config documents."""

    def __init__(self, ttl_seconds: float = 5.0) -> None:
        self._ttl_seconds = ttl_seconds
        self._entries: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def read(self, key: str, fetch: Callable[[], Any]) -> Any:
        """Return the cached value for ``key``, fetching and caching on miss/expiry."""
        now = time.monotonic()
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None and now - cached[0] < self._ttl_seconds:
                return cached[1]
        value = fetch()
        with self._lock:
            self._entries[key] = (now, value)
        return value

    def store(self, key: str, value: Any) -> None:
        """Write-through update so own writes are visible without a re-fetch."""
        with self._lock:
            self._entries[key] = (time.monotonic(), value)
