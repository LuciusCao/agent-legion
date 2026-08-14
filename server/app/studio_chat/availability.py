"""PATH-level availability probe for registry agent commands.

The picker API must only offer agents that can actually launch on this host,
and session creation must fail with a clear error before any spawn attempt.
Probing is deliberately shallow — ``shutil.which`` on the registry entry's
command, no ACP handshake — with a short TTL cache so listing endpoints do
not stat the PATH on every request. which/clock are injectable for tests.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from collections.abc import Callable

DEFAULT_TTL_SECONDS = 60.0


class AgentAvailabilityProbe:
    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        *,
        which: Callable[[str], str | None] = shutil.which,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._which = which
        self._clock = clock
        self._cache: dict[str, tuple[float, bool]] = {}
        self._lock = threading.Lock()

    def available(self, command: str) -> bool:
        command = os.path.expanduser(command)
        now = self._clock()
        with self._lock:
            cached = self._cache.get(command)
            if cached is not None and now - cached[0] < self._ttl:
                return cached[1]
        result = self._which(command) is not None
        with self._lock:
            self._cache[command] = (now, result)
        return result

    def probe_all(self, commands: list[str]) -> dict[str, bool]:
        """Startup warm-up: probe every command once, filling the cache."""
        return {command: self.available(command) for command in commands}
