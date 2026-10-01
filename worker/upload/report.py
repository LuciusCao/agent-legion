"""Final result-report loop for one prepared upload task."""

from __future__ import annotations

import shutil
import threading
from typing import Any

from worker.host.transfer import TransferOperations, TransferStopped
from worker.upload import heartbeat as upload_heartbeat
from worker.upload.cleanup import drop_marker
from worker.upload.control import CombinedStop
from worker.upload.task import UploadTask


def report_task(
    client: Any,
    task: UploadTask,
    shutdown: threading.Event,
    heartbeat_interval: float,
    *,
    retry_base_seconds: float,
    retry_cap_seconds: float,
    heartbeat_join_seconds: float,
) -> str:
    """Report once with lease-aware backoff; return the terminal queue outcome."""
    metadata = task.prepared_metadata or {}
    archive = task.prepared_archive or (task.execution_dir / "result.tar.gz")
    upload_heartbeat.quiesce_task_heartbeat(task, heartbeat_join_seconds)
    if task.report_timer is not None and archive.is_file():
        task.report_timer.archive_bytes = archive.stat().st_size
    backoff = retry_base_seconds
    status_code, body, lost = 0, b"", False
    while not shutdown.is_set():
        if task.ownership_lost.is_set():
            lost = True
            print(
                f"result report abandoned for {task.execution_id}: lease lost; discarding result",
                flush=True,
            )
            break
        stop = CombinedStop(shutdown, task.ownership_lost)
        try:
            if isinstance(client, TransferOperations):
                status_code, body = client.report(
                    task.execution_id, task.lease_id, metadata, archive, stop=stop
                )
            else:
                status_code, body = client.report(
                    task.execution_id, task.lease_id, metadata, archive
                )
        except TransferStopped:
            if task.ownership_lost.is_set():
                lost = True
                break
            return "aborted"
        except RuntimeError as exc:
            print(f"result report retry for {task.execution_id}: {exc}", flush=True)
            task.heartbeat_thread = upload_heartbeat.resume_upload_heartbeat(
                client, task, heartbeat_interval
            )
            stop.wait(backoff)
            backoff = min(backoff * 2, retry_cap_seconds)
            upload_heartbeat.quiesce_task_heartbeat(task, heartbeat_join_seconds)
            continue
        if status_code == 204:
            break
        print(
            f"result report rejected for {task.execution_id}: HTTP {status_code}: {body[:200]!r}",
            flush=True,
        )
        break
    else:
        return "aborted"
    if status_code == 204:
        drop_marker(task)
        shutil.rmtree(task.execution_dir, ignore_errors=True)
        return "delivered"
    drop_marker(task)
    return "lost" if lost else "rejected"
