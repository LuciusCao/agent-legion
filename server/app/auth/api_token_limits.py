"""Per-token request rate limit for workspace API tokens (#738, lean scope).

The entry guardrail of the external intake channel (#626): one bad-neighbour
token (polling storm, submission storm, leaked credential) must not force
every other caller — sibling tokens, studio cookie sessions — to share its
load. Every workspace API token gets its OWN token bucket (keyed by
token_id); the bucket parameters are instance-wide (env-only ``auth``
section, see ``limits_from_config``): ``requests_per_minute`` is the refill
rate, ``burst`` the bucket capacity.

Counter store: process memory (``InMemoryApiTokenLimiter``). A restart
clears the buckets (acceptable: the window is minute-level). Under a
multi-replica http plane every replica counts on its own (per-replica
best-effort, same philosophy as ``last_used_at``; see the #740 topology
doc). The ``ApiTokenLimiter`` protocol is the seam for a shared store later.

Pure logic: no FastAPI, no database (the HTTP mapping lives in
``api_token_identity``), so the bucket and Retry-After math is unit
testable with an injected clock.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

# Conservative defaults (#738). 60 req/min sustained is 15x what one caller
# polling the paginated job snapshot every 15 s needs; a burst of 20 absorbs
# a submit + immediate status/manifest fan-out without tripping.
DEFAULT_REQUESTS_PER_MINUTE = 60
DEFAULT_BURST = 20


@dataclass(frozen=True)
class ApiTokenLimits:
    requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE
    burst: int = DEFAULT_BURST

    def __post_init__(self) -> None:
        if self.requests_per_minute < 1 or self.burst < 1:
            raise ValueError("api token rate limit and burst must be >= 1")


def limits_from_config(config: dict[str, Any]) -> ApiTokenLimits:
    """Instance-wide limits from the env-only ``auth`` section
    (``AGENT_LEGION_API_TOKEN_RATE_LIMIT_PER_MINUTE`` / ``_BURST``, wired in
    configuration/env_overrides.py); absent keys take the defaults, invalid
    values fail the startup (ValueError)."""
    auth = config.get("auth")
    auth = auth if isinstance(auth, dict) else {}
    return ApiTokenLimits(
        requests_per_minute=int(
            auth.get("api_token_rate_limit_per_minute", DEFAULT_REQUESTS_PER_MINUTE)
        ),
        burst=int(auth.get("api_token_rate_limit_burst", DEFAULT_BURST)),
    )


@dataclass(frozen=True)
class LimitDecision:
    allowed: bool
    # Whole seconds until a retry can succeed (>= 1 when refused, 0 when
    # allowed) — the ``Retry-After`` header value.
    retry_after_seconds: int = 0


ALLOWED = LimitDecision(allowed=True)


class ApiTokenLimiter(Protocol):
    """Replaceable counter store (in-memory today, shared store later)."""

    # The effective bucket parameters, read-only exposed to the console
    # (#870: the 外部对接 section shows what a 429-ing caller is up against).
    limits: ApiTokenLimits

    def acquire_request(self, token_id: str) -> LimitDecision: ...


@dataclass
class _Bucket:
    tokens: float
    updated: float


class InMemoryApiTokenLimiter:
    """Process-local token buckets keyed by token_id.

    Memory is bounded by the number of tokens that ever authenticated in
    this process (admin-issued, so small).
    """

    def __init__(
        self,
        limits: ApiTokenLimits | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits or ApiTokenLimits()
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._buckets: dict[str, _Bucket] = {}

    def acquire_request(self, token_id: str) -> LimitDecision:
        rate = self.limits.requests_per_minute / 60.0
        capacity = float(self.limits.burst)
        now = self._monotonic()
        with self._lock:
            bucket = self._buckets.get(token_id)
            if bucket is None:
                bucket = self._buckets[token_id] = _Bucket(tokens=capacity, updated=now)
            elapsed = max(0.0, now - bucket.updated)
            bucket.tokens = min(capacity, bucket.tokens + elapsed * rate)
            bucket.updated = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return ALLOWED
            deficit = 1.0 - bucket.tokens
        return LimitDecision(allowed=False, retry_after_seconds=max(1, math.ceil(deficit / rate)))
