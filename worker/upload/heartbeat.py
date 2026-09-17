"""Lease-heartbeat lifecycle helpers for upload tasks.

Split out of ``queue.py`` so the queue module stays within its size
budget. The upload queue quiesces the heartbeat for the final report (the
report itself is the last proof of life) and resumes it only while a
transient report failure backs off.

Since #352 the per-execution heartbeat threads are gone: leases live in the
per-Worker batch registry and these helpers forward quiesce/prune to it.
Legacy mode (no registry, e.g. unit tests driving the single-beat loop)
keeps the old thread stop/join semantics.

#644: every arm (initial register, backoff resume, legacy thread) shares the
task's ``ownership_lost`` event, so a beat-plane lost verdict (batch 409
family) reaches ``_report`` regardless of which arm is currently beating —
and a resume can never resurrect an already-condemned lease or erase its
verdict.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from worker.execution.heartbeat_batch import BatchHeartbeatRegistry


def start_upload_heartbeat(client: Any, task: Any, interval: float) -> threading.Thread | None:
    """Keep one upload task's lease alive (registry mode, #352): arm the
    lease with the per-Worker batch coordinator (``thread`` is None — the
    coordinator owns the beats), wiring the registry entry to the task's
    SHARED ownership_lost event (#644) so a lost verdict is visible to
    ``_report`` and survives later re-arms. #644: the arm is
    NON-DISPLACING (``register_upload``) — this task may be an old
    attempt's queued/restored task racing a re-claim, and a displacing
    register here deleted the re-claimed attempt's entry (its new lease
    then expired unrenewed). A lease MISMATCH at arm time additionally
    fires the task's ownership_lost (#644 review): the old lease is dead
    by definition, and without the verdict the task's report loop would
    retry to the backoff cap forever (its pair-matched resume/quiesce can
    never find an entry, and no beat returns a lost verdict for a dead
    lease). Legacy mode (no registry, e.g. unit tests
    driving the single-beat loop): start a daemon single-beat thread on
    the same shared event."""
    if task.heartbeat_registry is not None:
        task.heartbeat_registry.register_upload(
            task.execution_id, task.lease_id, task.ownership_lost
        )
        return None
    from worker.execution.lifecycle import HeartbeatConfig, heartbeat_loop

    config = HeartbeatConfig(
        client=client,
        execution_id=task.execution_id,
        lease_id=task.lease_id,
        stop=task.heartbeat_stop,
        interval=interval,
        ownership_lost=task.ownership_lost,
    )
    thread = threading.Thread(target=heartbeat_loop, args=(config,), daemon=True)
    thread.start()
    return thread


def resume_upload_heartbeat(client: Any, task: Any, interval: float) -> threading.Thread | None:
    """Re-arm one task's lease heartbeat for a report-backoff window (#644).

    Registry mode RESUMES the existing entry — pair-matched on
    (execution_id, lease_id) — instead of re-registering: ``register`` keys
    on execution_id alone, so the old re-arm path installed a brand-new
    entry that (a) erased an already-fired lost verdict with a fresh event
    and re-admitted the dead lease to every batch beat (the sustained
    same-execution 409 storm of #644), and (b) stomped a re-claimed
    attempt's NEW lease entry, silently stopping its beats. A lost entry
    stays excluded from the snapshot even after resume — resume only clears
    the quiesce flag. Legacy mode restarts the single-beat thread (quiesce
    stopped and joined it) on the shared ownership_lost event."""
    if task.heartbeat_registry is not None:
        task.heartbeat_registry.resume(task.execution_id, task.lease_id)
        return None
    task.heartbeat_stop = threading.Event()
    return start_upload_heartbeat(client, task, interval)


def prune_heartbeat(
    registry: BatchHeartbeatRegistry | None,
    stop: threading.Event,
    execution_id: str,
    lease_id: str = "",
) -> None:
    """Final stop for one lease: batch prune, or legacy thread stop.

    Registry prune is pair-matched (execution_id + lease_id): a Host requeue
    the Worker re-claimed installs a NEW entry under the same execution_id,
    and this (old attempt's) final stop must leave it alone."""
    if registry is not None:
        registry.prune(execution_id, lease_id)
        return
    stop.set()


def quiesce_task_heartbeat(task: Any, join_seconds: float) -> None:
    """Quiesce the upload task's heartbeat and clear its thread handle.

    Registry mode pauses the lease's beats, pair-matched on the task's own
    (execution_id, lease_id) (the report in flight is the last proof of
    life; a beat racing the commit logs a spurious 409). Legacy mode stops
    the thread and waits out any in-flight beat."""
    registry = getattr(task, "heartbeat_registry", None)
    if registry is not None:
        registry.quiesce(task.execution_id, task.lease_id)
        return
    task.heartbeat_stop.set()
    if task.heartbeat_thread is not None:
        task.heartbeat_thread.join(timeout=join_seconds)
        task.heartbeat_thread = None
