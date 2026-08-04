"""Lease-heartbeat lifecycle helpers for upload tasks.

Split out of ``upload_queue.py`` so the queue module stays within its size
budget. The upload queue quiesces the heartbeat for the final report (the
report itself is the last proof of life) and resumes it only while a
transient report failure backs off.
"""

from __future__ import annotations

import threading
from typing import Any

from worker.execution_lifecycle import HeartbeatConfig, heartbeat_loop


def start_upload_heartbeat(
    client: Any,
    execution_id: str,
    lease_id: str,
    stop: threading.Event,
    interval: float,
) -> threading.Thread:
    """Start a daemon heartbeat thread for one execution's lease."""
    # Upload-side beats outlive the agent process; the process-tracking
    # HeartbeatConfig fields stay inert defaults here.
    config = HeartbeatConfig(
        client=client, execution_id=execution_id, lease_id=lease_id, stop=stop, interval=interval
    )
    thread = threading.Thread(target=heartbeat_loop, args=(config,), daemon=True)
    thread.start()
    return thread


def quiesce_heartbeat(
    stop: threading.Event, thread: threading.Thread | None, join_seconds: float
) -> None:
    """Stop the lease heartbeat and wait out any in-flight beat."""
    stop.set()
    if thread is not None:
        thread.join(timeout=join_seconds)


def quiesce_task_heartbeat(task: Any, join_seconds: float) -> None:
    """Quiesce the upload task's heartbeat and clear its thread handle."""
    quiesce_heartbeat(task.heartbeat_stop, task.heartbeat_thread, join_seconds)
    task.heartbeat_thread = None
