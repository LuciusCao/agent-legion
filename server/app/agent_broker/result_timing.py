"""Result-stage timing for the Worker result commit path (issue #521).

The Host control plane is one process; when a completion wave (a DAG's
same-phase nodes exiting together) hits ``/result``, dozens of commits
enter the shared threadpool and the GIL-bound work inside them — tar
unpack, artifact verify/download, output validation, write transactions,
events.jsonl post-processing — saturates the single CPU core and starves
the claim loop and heartbeats. This module splits one result commit into
the stages that decide WHERE that CPU goes, mirroring the claim-stage
split (#448 phase 1):

- a log record per commit — DEBUG normally, WARNING past a threshold
  (default 15s, ``AGENT_LEGION_SLOW_RESULT_MS`` overrides; the
  slow-claim precedent) — carrying the per-stage millisecond breakdown;
- ``profile.note_result_stages`` into the #359 runtime profile, so the
  per-minute bucket keeps per-stage totals and maxes — the data that
  orders the follow-up (per-stage slimming; the events.jsonl
  single-pass merge is reserved for 0.7.3 and will be justified from
  the ``events`` column).

Stage boundaries (each segment spans from the previous boundary to its
own — a skipped stage folds its wall time into the next marked stage,
the same residual discipline as the claim timer):

- unpack: ownership recheck + archive rename + job fetch + tar/gzip
  extraction of the Worker archive;
- artifacts_verify: Worker-direct S3 ref verify/download/promote plus
  legacy CAS ref registration;
- validate: Host-side output validation (skill materialization + the
  sandbox validation subprocess);
- artifacts_upload: produced-artifact mirror into object storage;
- lease_write: the lease finish write transaction (leases/node_runs/
  job_nodes/jobs terminal state);
- events: the promoted events.jsonl post-processing — token usage
  parse + persist, then PI compression (two full scans today);
- mark_done: the agent_execution_requests terminal write.

The route-level ``result_timer`` (#359) spans spool + gate queue wait +
commit, so both the spool wall time and the #521 gate's queue wait are
the residual of ``result_seconds_total`` minus the stage sum (the queue
wait is the number an operator wants when evaluating the gate);
everything after ``mark_done`` (the finished-event emit, bundle
retirement) is likewise covered by the result-wide total.

Cost discipline mirrors ``claim_timing`` (this also runs per report at
fleet frequency): the timer is one dict of floats; ``stage`` does one
``perf_counter`` call and one dict store.
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

_DEFAULT_SLOW_RESULT_MS = 15000.0
_SLOW_RESULT_MS_ENV = "AGENT_LEGION_SLOW_RESULT_MS"

# Fixed output order of the log line's stage segments — every stage the
# commit path measures (see the module docstring for the boundaries).
_STAGE_ORDER = (
    "unpack",
    "artifacts_verify",
    "validate",
    "artifacts_upload",
    "lease_write",
    "events",
    "mark_done",
)


def slow_result_threshold_ms() -> float:
    """WARNING threshold in ms; malformed env overrides are ignored."""
    raw = os.environ.get(_SLOW_RESULT_MS_ENV, "")
    try:
        return float(raw) if raw else _DEFAULT_SLOW_RESULT_MS
    except ValueError:
        return _DEFAULT_SLOW_RESULT_MS


def _slow_result_threshold_ms() -> float:
    # Env read per call (one os.environ lookup) — same reasoning as
    # claim_timing: keeps the threshold live for test monkeypatching and
    # operators reloading env without a restart.
    return slow_result_threshold_ms()


class ResultStageTimer:
    """Accumulates per-stage seconds for one result commit attempt."""

    __slots__ = ("stages", "_start")

    def __init__(self) -> None:
        self.stages: dict[str, float] = {}
        self._start = time.perf_counter()

    def stage(self, name: str) -> None:
        """Close one stage: add elapsed time since the previous stage call."""
        now = time.perf_counter()
        self.stages[name] = self.stages.get(name, 0.0) + (now - self._start)
        self._start = now


def mark(timer: ResultStageTimer | None, name: str) -> None:
    """Close one stage on an optional timer.

    The timer threads from ``commit_agent_result`` through
    ``completion.finish`` into the lease write path; the code-plane
    callers of those functions pass ``None`` and must stay untouched —
    this one-liner keeps their call sites free of ``is not None`` noise.
    """

    if timer is not None:
        timer.stage(name)


def log_result_stages(
    stages: dict[str, float], *, execution_id: str, worker_id: str, committed: bool
) -> None:
    """Log one result commit's stage breakdown: DEBUG normally, WARNING when slow."""
    if not stages:
        return
    total_ms = sum(stages.values()) * 1000.0
    # One threshold read (subagent review on #530): reading it twice can
    # disagree between the guard and the level choice if the env changes
    # in between.
    threshold_ms = _slow_result_threshold_ms()
    if total_ms <= threshold_ms and not logger.isEnabledFor(logging.DEBUG):
        return
    parts = " ".join(
        f"{name}={stages[name] * 1000.0:.1f}ms" for name in _STAGE_ORDER if name in stages
    )
    message = (
        "result stages: %s total=%.1fms execution=%s worker=%s committed=%s",
        parts,
        total_ms,
        execution_id,
        worker_id,
        committed,
    )
    if total_ms > threshold_ms:
        logger.warning(*message)
    else:
        logger.debug(*message)


def note_result_stages(stages: dict[str, float]) -> None:
    """Fold one commit's stage timings into the #359 runtime profile.

    The runtime-profile discipline (counters.py): best-effort, never
    raises, undercount is acceptable. Imported lazily so a profile wiring
    failure cannot take the result path down. Result COUNTING
    (result_count / result_seconds_*) is note_result's exclusive job on
    the route — the stage fold never touches those (the #461
    double-counting lesson from the claim side).
    """
    try:
        from server.app.services.runtime_profile import profile

        profile.note_result_stages(stages)
    except Exception:
        # #204 broad-except audit: instrumentation must never break the
        # result commit it observes. The outcome space is any
        # import/attribute failure inside the profile module; swallowing
        # only loses metrics, and the commit itself (already committed or
        # about to return) stays intact. No log line: this fires per
        # report, and an import failure would repeat at fleet frequency —
        # the missing metric is itself visible in the profile UI.
        pass


def report_result_stages(
    timer: ResultStageTimer, *, execution_id: str, worker_id: str, committed: bool
) -> None:
    """Log + profile one commit attempt's stage timings (best-effort).

    Called from ``commit_agent_result``'s finally: every exit path — the
    409 ownership rejections included — reports what it measured, the
    same attempt-level discipline the claim timer keeps inside the
    transaction (#498 contrast: the commit EVENTS describe committed
    state and stay on the success path only).
    """
    if not timer.stages:
        return
    log_result_stages(
        timer.stages, execution_id=execution_id, worker_id=worker_id, committed=committed
    )
    note_result_stages(timer.stages)
