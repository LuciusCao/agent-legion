"""Short-TTL process-internal cache for the job list first-screen aggregates.

Issue #358: the workspace job list's first page triggers
``count_jobs_filtered`` (a full COUNT over the workspace's filtered jobs
slice) plus 4+ facet group-bys over the same slice. On a 10^5+-job
workspace every refresh re-scans the whole slice; the run of a job-list
polling loop turns that into a constant background load on the shared
connection pool.

The facets and the filtered total are first-screen UI only: the live
per-status stats already stream over SSE from the trigger-maintained
workspace counters, and the frontend treats facet counts as advisory
navigation hints. A 7s TTL process-local cache therefore keeps the API
shape byte-identical while collapsing a refresh storm into one scan per
window per filter.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Hashable
from typing import TypeVar, cast

T = TypeVar("T")

# Issue #358 suggests 5–10s; 7s sits in the middle and stays well under the
# shortest frontend refetch cadence that consumes these aggregates.
DEFAULT_TTL_SECONDS = 7.0


class TtlCache:
    """A minimal size-bounded TTL cache (no eviction thread).

    Entries expire lazily on read; the bound is a soft cap enforced by
    dropping the oldest entries when the cache grows past it, which is
    enough for the filter-combination cardinality this serves (a handful of
    filter shapes per workspace).
    """

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS, max_entries: int = 256):
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._entries: dict[Hashable, tuple[float, object]] = {}

    def get_or_compute(self, key: Hashable, compute: Callable[[], T]) -> T:
        """Return the cached value for ``key`` or compute, cache, and return it."""
        now = time.monotonic()
        hit = self._entries.get(key)
        if hit is not None and now - hit[0] < self._ttl:
            return cast(T, hit[1])
        value = compute()
        if len(self._entries) >= self._max_entries:
            # Soft bound: drop the oldest inserted entries (dicts keep
            # insertion order; no LRU bookkeeping needed at this cardinality).
            for stale_key in list(self._entries)[: len(self._entries) - self._max_entries + 1]:
                self._entries.pop(stale_key, None)
        self._entries[key] = (now, value)
        return value

    def clear(self) -> None:
        self._entries.clear()
