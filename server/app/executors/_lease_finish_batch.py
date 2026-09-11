"""Batched lease-finish transaction for the #591 result group-commit.

Sibling of ``_lease_write_paths`` (same package-private seam): the direct
``finish`` stays where every code-plane caller reaches it; this module owns
the drained-wave arm the batcher's writer thread runs — N ``finish_lease``
bodies inside ONE ``write_transaction``. Per-item semantics are identical
to the direct path (``finish_lease`` re-selects the lease and returns False
for a non-active row — a 409 is data, not an error).

Two disciplines the codex review on #609 added:

- **Deterministic counter-lock order** (#591 C4, full key per #609 P1-2):
  ``sync_job_status``'s statement triggers take (run_id, status) and
  (workspace_id, status) counter rows per jobs UPDATE, and a multi-job
  batch writing in queue order can interleave with ``try_claim_many``'s
  multi-job claim transaction as A→B vs B→A (SQLSTATE 40P01) — a job_id
  sort alone does NOT pin that order, because two jobs sorted X→Y by
  job_id can live in workspaces ordered Y→X. Both batches therefore
  resolve every item's (workspace_id, run_id, job_id) up front (the jobs
  row is the key the triggers read) and write in that shared order.
  Residual windows, documented rather than expanded (P2): the per-item
  statement still takes its run-counter rows before its workspace-counter
  row (trigger firing order is alphabetical), so the workspace row's
  position sits after each batch's OWN first item in that workspace — two
  multi-item batches whose first items in one SHARED workspace differ can
  still close a ring on (workspace row, run row); the sweep/expire paths
  (``expire_stale_leases`` and friends) also touch counters in arrival
  order. Both are far narrower than the cross-workspace opposite-order
  class this removes and stay covered by the 40P01 retry; the airtight
  shapes (workspace-trigger-first firing order, or one multi-row jobs
  UPDATE aggregating the whole batch through the statement trigger) are
  schema-level changes out of scope here.
- **The writer thread owns only the shared transaction** (#591 C5): events
  post-processing (token capture + PI compression, two full-file scans per
  item) is returned to the SUBMITTING commit thread as per-item closures —
  the original parallel shape. A large events.jsonl on the single writer
  would head-of-line-block every queued verdict; on a commit thread it
  parks only that request's own reporter.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from server.app.db.retry import retry_on_database_conflict
from server.app.db.transaction import write_transaction
from server.app.executors._lease_lifecycle import finish_lease
from server.app.executors._lease_write_paths import (
    _mark_result_stage,
    finish_events_post_processing,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from server.app.executors.leases import ExecutorLeaseRepository
    from server.app.executors.models import ExecutionResult

logger = logging.getLogger(__name__)


def finish_many(
    repo: ExecutorLeaseRepository,
    writes: list[tuple[str, ExecutionResult, Any]],
) -> tuple[list[bool], list[Callable[[], None] | None]]:
    """Commit a drained completion wave's lease finishes in ONE transaction.

    Each entry is a (lease_id, result, stage_timer) the caller parked on
    the batcher queue. Returns the per-item verdicts (queue order) plus
    post-commit callables the caller's ``submit`` runs on the submitting
    thread: the #521 stage marks, events post-processing for the
    completed/failed family, and the deduplicated per-job broadcast
    (record_job_update reads current stats, so N broadcasts are noise).
    """
    with write_transaction(repo.path) as conn:
        # (workspace_id, run_id, job_id, queue_index, lease_id, result, timer):
        # the first three are the counter keys the status triggers read —
        # the full sort key #609 P1-2 pins (see the module docstring), with
        # queue position for stability. Verdicts are re-assembled in QUEUE
        # order below so each submitting thread gets its own item's answer.
        resolved: list[tuple[str, str, str, int, str, ExecutionResult, Any]] = []
        for index, (lease_id, result, stage_timer) in enumerate(writes):
            lease = conn.execute(
                "select l.job_id, j.workspace_id, j.run_id from executor_leases l"
                " left join jobs j on j.id = l.job_id where l.id = %s",
                (lease_id,),
            ).fetchone()
            resolved.append(
                (
                    str(lease.get("workspace_id") or "") if lease else "",
                    str(lease.get("run_id") or "") if lease else "",
                    str(lease.get("job_id")) if lease else "",
                    index,
                    lease_id,
                    result,
                    stage_timer,
                )
            )
        resolved.sort(key=lambda entry: entry[:4])
        by_index: dict[int, bool] = {}
        for _ws, _run, _job_id, index, lease_id, result, _timer in resolved:
            by_index[index] = finish_lease(conn, lease_id, result, repo.data_dir)
    outcomes = [by_index.get(index, False) for index in range(len(writes))]

    # Post-commit work, back to the submitting thread (#591 C5). The #609
    # review round pins the direct path's two gates on the callback:
    # - events family gate: token capture + PI compression run ONLY for
    #   completed/failed results — a cancelled outcome must not parse the
    #   partial events.jsonl, persist token usage the direct path never
    #   writes, or compress the file the direct path leaves intact for
    #   debugging;
    # - #521 stage marks: lease_write closes at the callback's first
    #   statement (under batching it spans queue wait + the shared batch —
    #   the honest measurement, the stage data that motivated #591),
    #   events closes after the events work, and a cancelled or 409 item
    #   never reports an events segment (#530: no misleading events=0.0ms).
    # The broadcast keeps the direct path's verdict-only gate — a cancelled
    # finish still refreshes the job's SSE stats.
    callbacks: list[Callable[[], None] | None] = [None] * len(writes)
    claimed_jobs: set[str] = set()

    def _post_for(
        job_id: str,
        lease_id: str,
        result: ExecutionResult,
        stage_timer: Any,
        events_ran: bool,
        with_broadcast: bool,
    ) -> Callable[[], None]:
        def run() -> None:
            _mark_result_stage(stage_timer, "lease_write")
            if events_ran:
                _finish_post_processing(repo, lease_id, result)
                _mark_result_stage(stage_timer, "events")
            if with_broadcast:
                repo._broadcast_job_update(job_id)

        return run

    for _ws, _run, job_id, index, lease_id, result, stage_timer in sorted(
        resolved, key=lambda i: i[3]
    ):
        result_flag = by_index[index]
        events_ran = result_flag and result.status in ("completed", "failed")
        with_broadcast = result_flag and job_id not in claimed_jobs
        if result_flag:
            claimed_jobs.add(job_id)
        if result_flag or stage_timer is not None:
            post = _post_for(job_id, lease_id, result, stage_timer, events_ran, with_broadcast)
            callbacks[index] = post
    return outcomes, callbacks


def _finish_post_processing(
    repo: ExecutorLeaseRepository, lease_id: str, result: ExecutionResult
) -> None:
    """Events post-processing for the post-commit callbacks: the shared
    token-capture + PI-compression shape lives with the direct path
    (``finish_events_post_processing`` in _lease_write_paths) — one owner
    for both finish paths (#591 C5)."""
    finish_events_post_processing(repo, lease_id, result)


def finish_many_with_retry(
    repo: ExecutorLeaseRepository,
    writes: list[tuple[str, ExecutionResult, Any]],
) -> tuple[list[bool], list[Callable[[], None] | None]]:
    """#591 batched arm entry: the retry wrapper the batcher binds."""
    return retry_on_database_conflict(lambda: finish_many(repo, writes))


def finish_via_batcher(
    repo: ExecutorLeaseRepository,
    batcher: Any,
    lease_id: str,
    result: ExecutionResult,
    stage_timer: Any,
) -> bool:  # typed: returns the batcher verdict or the serial write's
    """The repo's finish() batched entry: park on the queue, with the
    serial write as the post-stop fallback (#591 C6)."""
    from server.app.executors import _lease_write_paths

    def direct() -> bool:
        return cast(
            bool, _lease_write_paths.finish(repo, lease_id, result, stage_timer=stage_timer)
        )

    direct_with_retry = lambda: retry_on_database_conflict(direct)  # noqa: E731
    if batcher is None:
        return direct_with_retry()
    # #521 stage marks: submit() runs the item's post-commit callback on
    # THIS thread before returning — _post_for closes lease_write (queue
    # wait + batch, the honest batching measurement) and events there. A
    # mark placed after submit() returns would be wrong twice over: the
    # events work has already run (submit runs the callbacks), so its time
    # would fold into lease_write and the two segments would swap order.
    verdict = batcher.submit("finish", (lease_id, result, stage_timer), direct=direct_with_retry)
    return cast(bool, verdict)
