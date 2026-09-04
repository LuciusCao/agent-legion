"""Claim-stage timing for the Worker claim path (issue #448 phase 1).

The worker claim loop is single-threaded serial: claim throughput equals
1 / (one claim round-trip), so the forensic question #448 must answer is
WHERE inside the claim transaction the round-trip goes (worker setup,
candidate scan, per-candidate lock/evaluate, promote writes, commit). This
module measures those stages with ``time.perf_counter`` at the claim call
sites and reports them two ways:

- a log record per claim — DEBUG normally, WARNING past a threshold
  (default 5s, ``AGENT_LEGION_SLOW_CLAIM_MS`` overrides; the slow-request
  middleware precedent, one env read at import) — carrying the per-stage
  millisecond breakdown;
- ``profile.note_claim_stages`` into the #359 runtime profile, so the
  per-minute bucket keeps scan/evaluate/writes totals and maxes — the data
  that decides phase 2's priority (transaction slimming vs concurrency vs
  event-driven wakeups).

Cost discipline (this runs on the fleet's highest-frequency call): the
timer is one dict of floats; ``stage`` does a ``perf_counter`` call and one
dict store; no log record is built below the fired level and no formatting
happens when neither level is enabled.
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

_DEFAULT_SLOW_CLAIM_MS = 5000.0
_SLOW_CLAIM_MS_ENV = "AGENT_LEGION_SLOW_CLAIM_MS"

# Fixed output order of the log line's stage segments.
_STAGE_ORDER = ("worker_setup", "scan", "evaluate", "writes", "commit")


def slow_claim_threshold_ms() -> float:
    """WARNING threshold in ms; malformed env overrides are ignored."""
    raw = os.environ.get(_SLOW_CLAIM_MS_ENV, "")
    try:
        return float(raw) if raw else _DEFAULT_SLOW_CLAIM_MS
    except ValueError:
        return _DEFAULT_SLOW_CLAIM_MS


def _slow_claim_threshold_ms() -> float:
    # Env read per call today (one os.environ lookup, ~100ns) — see the
    # module docstring's cost discipline; hoisting to import time would
    # freeze the threshold for test monkeypatching and for operators
    # reloading env without a restart.
    return slow_claim_threshold_ms()


class ClaimStageTimer:
    """Accumulates per-stage seconds for one claim transaction attempt."""

    __slots__ = ("stages", "_start")

    def __init__(self) -> None:
        self.stages: dict[str, float] = {}
        self._start = time.perf_counter()

    def stage(self, name: str) -> None:
        """Close one stage: add elapsed time since the previous stage call."""
        now = time.perf_counter()
        self.stages[name] = self.stages.get(name, 0.0) + (now - self._start)
        self._start = now

    def note(self, name: str, seconds: float) -> None:
        """Add an externally measured duration to a stage (e.g. commit)."""
        self.stages[name] = self.stages.get(name, 0.0) + seconds


def log_claim_stages(
    stages: dict[str, float], *, worker_id: str, claimed: bool, attempts: int, skipped: int
) -> None:
    """Log one claim's stage breakdown: DEBUG normally, WARNING when slow."""
    total_ms = sum(stages.values()) * 1000.0
    if total_ms <= _slow_claim_threshold_ms() and not logger.isEnabledFor(logging.DEBUG):
        return
    parts = " ".join(
        f"{name}={stages[name] * 1000.0:.1f}ms" for name in _STAGE_ORDER if name in stages
    )
    message = (
        "claim stages: %s total=%.1fms worker=%s claimed=%s attempts=%d skipped=%d",
        parts,
        total_ms,
        worker_id,
        claimed,
        attempts,
        skipped,
    )
    if total_ms > _slow_claim_threshold_ms():
        logger.warning(*message)
    else:
        logger.debug(*message)


def note_claim_stages(stages: dict[str, float], *, claimed: bool) -> None:
    """Fold one claim's stage timings into the #359 runtime profile.

    The runtime-profile discipline (counters.py): best-effort, never raises,
    undercount is acceptable. Imported lazily so a profile wiring failure
    cannot take the claim path down.
    """
    try:
        from server.app.services.runtime_profile import profile

        profile.note_claim_stages(stages, claimed=claimed)
    except Exception:
        # #204 broad-except audit: instrumentation must never break the
        # claim it observes. The outcome space is any import/attribute
        # failure inside the profile module; swallowing only loses metrics,
        # and the claim itself (already committed or about to return) stays
        # intact. No log line: this fires per claim, and an import failure
        # would repeat at fleet frequency — the missing metric is itself
        # visible in the profile UI.
        pass
