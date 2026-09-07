"""Claim-loop backoff sequence for the Worker supervisor (issue #437).

The old loop waited ``poll_interval`` on the first claim failure and doubled
afterwards with no jitter: a Host blip (or the #437 claim deadlocks) took
the whole fleet's claim line down in lockstep, and the fixed progression
kept it down — the completion flow drained concurrency slots meanwhile,
which showed up as sawtooth concurrency under the high-concurrency tier.

The new sequence: a short fixed first delay (1s — a transient blip should
not cost a full poll interval), then exponential doubling from there with
±20% jitter so a fleet recovering from the same outage spreads its retries,
capped at ``CLAIM_BACKOFF_CAP_SECONDS``. The jitter source is injectable so
the sequence is deterministically testable.
"""

from __future__ import annotations

import random
from collections.abc import Callable

# First failure waits this long regardless of poll_interval (seconds).
CLAIM_BACKOFF_FIRST_SECONDS = 1.0
# ±20% jitter band around the deterministic progression.
CLAIM_BACKOFF_JITTER = 0.2
# Upper bound of the wait progression (unchanged by #437).
CLAIM_BACKOFF_CAP_SECONDS = 60.0


def jittered_claim_backoff(
    failures: int,
    *,
    first_seconds: float = CLAIM_BACKOFF_FIRST_SECONDS,
    cap_seconds: float = CLAIM_BACKOFF_CAP_SECONDS,
    jitter: float = CLAIM_BACKOFF_JITTER,
    rng: Callable[[], float] | None = None,
) -> float:
    """The wait before retry ``failures + 1`` (0-based consecutive-failure count).

    ``failures=0`` returns the fixed short first delay; every later wait
    doubles from there (``first * 2**(failures-1)``) with ±``jitter``
    uniform jitter, clamped to ``[deterministic * (1-jitter), cap_seconds]``
    — jitter can lower a wait but never push it past the cap.
    """
    if failures <= 0:
        return first_seconds
    draw = rng() if rng is not None else random.random()
    # float() pins int.__pow__'s Any return to a plain float (mypy no-any-return).
    deterministic = min(first_seconds * 2.0 ** (failures - 1), cap_seconds)
    jittered = deterministic * (1.0 + (float(draw) * 2.0 - 1.0) * jitter)
    return max(deterministic * (1.0 - jitter), min(jittered, cap_seconds))


class ClaimBackoffSequence:
    """Stateful backoff for the claim loop: call ``next_wait()`` after each
    failure, ``reset()`` after a successful claim pass."""

    def __init__(
        self,
        *,
        cap_seconds: float = CLAIM_BACKOFF_CAP_SECONDS,
        first_seconds: float = CLAIM_BACKOFF_FIRST_SECONDS,
        jitter: float = CLAIM_BACKOFF_JITTER,
        rng: Callable[[], float] | None = None,
    ) -> None:
        # Consecutive failures so far; #490's claim.backoff event reads it.
        self.failures = 0
        self._cap_seconds = cap_seconds
        self._first_seconds = first_seconds
        self._jitter = jitter
        self._rng = rng

    def next_wait(self) -> float:
        wait = jittered_claim_backoff(
            self.failures,
            first_seconds=self._first_seconds,
            cap_seconds=self._cap_seconds,
            jitter=self._jitter,
            rng=self._rng,
        )
        self.failures += 1
        return wait

    def reset(self) -> None:
        self.failures = 0
