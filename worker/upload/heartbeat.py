"""Lease-heartbeat lifecycle helpers for upload tasks.

Split out of ``queue.py`` so the queue module stays within its size
budget. The upload queue quiesces the heartbeat for the final report (the
report itself is the last proof of life) and resumes it only while a
transient report failure backs off.

Since #352 the per-execution heartbeat threads are gone: leases live in the
per-Worker batch registry and these helpers forward quiesce/prune to it.
Legacy mode (no registry, e.g. unit tests driving the single-beat loop)
keeps the old thread stop/join semantics.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from worker.execution.heartbeat_batch import BatchHeartbeatRegistry


def start_upload_heartbeat(client: Any, task: Any, interval: float) -> threading.Thread | None:
    """Keep one upload task's lease alive (registry mode, #352): register the
    lease with the per-Worker batch coordinator (``thread`` is None — the
    coordinator owns the beats); re-registration on report backoff doubles as
    the resume path. Legacy mode (no registry, e.g. unit tests driving the
    single-beat loop): start a daemon single-beat thread."""
    if task.heartbeat_registry is not None:
        task.heartbeat_registry.register(task.execution_id, task.lease_id, threading.Event())
        return None
    from worker.execution.lifecycle import HeartbeatConfig, heartbeat_loop

    config = HeartbeatConfig(
        client=client,
        execution_id=task.execution_id,
        lease_id=task.lease_id,
        stop=task.heartbeat_stop,
        interval=interval,
    )
    thread = threading.Thread(target=heartbeat_loop, args=(config,), daemon=True)
    thread.start()
    return thread


def prune_heartbeat(
    registry: BatchHeartbeatRegistry | None, stop: threading.Event, execution_id: str
) -> None:
    """Final stop for one lease: batch prune, or legacy thread stop."""
    if registry is not None:
        registry.prune(execution_id)
        return
    stop.set()


def quiesce_task_heartbeat(task: Any, join_seconds: float) -> None:
    """Quiesce the upload task's heartbeat and clear its thread handle.

    Registry mode pauses the lease's beats (the report in flight is the last
    proof of life; a beat racing the commit logs a spurious 409). Legacy
    mode stops the thread and waits out any in-flight beat."""
    registry = getattr(task, "heartbeat_registry", None)
    if registry is not None:
        registry.quiesce(task.execution_id)
        return
    task.heartbeat_stop.set()
    if task.heartbeat_thread is not None:
        task.heartbeat_thread.join(timeout=join_seconds)
        task.heartbeat_thread = None
