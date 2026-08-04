"""Bounded, disk-backed result upload queue for Agent Worker executions.

Decouples "the Agent process finished" from "the result reached the Host":
the execution thread releases its slot at process exit and hands everything
after that point — model-error scan, events compression, archive build,
artifact upload, result report — to this queue. Upload concurrency stays
small (default 4) so a completion wave of dozens of executions never turns
into a transfer storm against the Host.

Durability: every task writes an ``upload_pending.json`` marker into its
execution dir before entering the queue; the marker is removed only after
the Host accepts the result. A crashed Worker rescans it on startup.

Lease ownership: the per-execution heartbeat started at claim time keeps
beating through the upload. It is quiesced for the final report and resumed
only while a transient report failure backs off (worker/upload_heartbeat.py).
"""

from __future__ import annotations

import json
import shutil
import tarfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from server.app.services.pi_event_compression import (
    compress_pi_events,
    scan_and_compress_pi_events,
)
from worker import upload_heartbeat
from worker._retry import run_with_retry
from worker.host_transfer import HostRequestError

PENDING_FILENAME = "upload_pending.json"
_PENDING_VERSION = 1

_RETRY_BASE_SECONDS = 2.0
_RETRY_CAP_SECONDS = 60.0
MAX_ERROR_MESSAGE_CHARS = 4000
_HEARTBEAT_JOIN_SECONDS = 5.0


@dataclass
class UploadTask:
    """Everything needed to deliver one execution's result to the Host."""

    execution_id: str
    lease_id: str
    execution_dir: Path
    node_key: str
    status_fields: dict[str, str]
    # "process": run post-processing (scan/compress/archive) then report.
    # "prebuilt": metadata is final (pre-process failure / pre-start cancel).
    kind: str
    exit_code: int = 1
    expected_outputs: tuple[str, ...] = ()
    command: tuple[str, ...] = ()
    prebuilt_metadata: dict[str, Any] | None = None
    heartbeat_stop: threading.Event = field(default_factory=threading.Event)
    heartbeat_thread: threading.Thread | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "version": _PENDING_VERSION,
            "execution_id": self.execution_id,
            "lease_id": self.lease_id,
            "node_key": self.node_key,
            "status_fields": self.status_fields,
            "kind": self.kind,
            "exit_code": self.exit_code,
            "expected_outputs": list(self.expected_outputs),
            "command": list(self.command),
            "prebuilt_metadata": self.prebuilt_metadata,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any], work_root: Path) -> UploadTask:
        if int(payload.get("version", 0)) != _PENDING_VERSION:
            raise ValueError(f"unsupported upload marker version: {payload.get('version')!r}")
        execution_id = str(payload["execution_id"])
        return cls(
            execution_id=execution_id,
            lease_id=str(payload["lease_id"]),
            execution_dir=work_root / execution_id,
            node_key=str(payload["node_key"]),
            status_fields={str(k): str(v) for k, v in dict(payload["status_fields"]).items()},
            kind=str(payload["kind"]),
            exit_code=int(payload.get("exit_code", 1)),
            expected_outputs=tuple(str(name) for name in payload.get("expected_outputs", [])),
            command=tuple(str(part) for part in payload.get("command", [])),
            prebuilt_metadata=payload.get("prebuilt_metadata"),
        )


def _write_empty_archive(archive: Path) -> None:
    with tarfile.open(archive, "w:gz"):
        pass


class UploadQueue:
    def __init__(
        self,
        client: Any,
        status: Any,
        *,
        max_concurrency: int = 4,
        heartbeat_interval: float = 15.0,
        stop: threading.Event | None = None,
    ) -> None:
        self._client = client
        self._status = status
        self._heartbeat_interval = heartbeat_interval
        self._stop = stop if stop is not None else threading.Event()
        self._pool = ThreadPoolExecutor(max_concurrency, thread_name_prefix="agent-upload")
        self._lock = threading.Lock()
        self._depth = 0

    @property
    def depth(self) -> int:
        """Tasks queued or in flight; the claim loop reads this for backpressure."""
        with self._lock:
            return self._depth

    def submit(self, task: UploadTask) -> None:
        """Persist the pending marker, then queue the delivery.

        Marker first: a crash between the two loses at most the result (the
        Host requeues after the lease expires), never reports twice.
        """
        marker = task.execution_dir / PENDING_FILENAME
        marker.write_text(json.dumps(task.to_json(), ensure_ascii=False), encoding="utf-8")
        # upsert: 重启恢复的任务在 reporter 里尚无条目，积压期间也要以 queued_upload 可见。
        self._status.upsert_phase(task.execution_id, "queued_upload", **task.status_fields)
        with self._lock:
            self._depth += 1
        self._pool.submit(self._run, task)

    def restore(self, work_root: Path) -> int:
        """Re-queue executions whose results never reached the Host."""
        restored = 0
        try:
            children = sorted(work_root.iterdir())
        except OSError:
            return 0
        for child in children:
            marker = child / PENDING_FILENAME
            if not child.is_dir() or not marker.is_file():
                continue
            try:
                task = UploadTask.from_json(
                    json.loads(marker.read_text(encoding="utf-8")), work_root
                )
            except Exception as exc:
                print(f"discarding unreadable upload marker {marker}: {exc}", flush=True)
                shutil.rmtree(child, ignore_errors=True)
                continue
            self.submit(task)
            restored += 1
        return restored

    def shutdown(self) -> None:
        # Tasks watch the shared stop event and bail out of retry loops quickly.
        self._pool.shutdown(wait=True)

    def _run(self, task: UploadTask) -> None:
        if task.heartbeat_thread is None:
            # Restored from disk: resume heartbeating so the lease survives.
            # The status entry already exists — submit() upserted it at restore.
            task.heartbeat_thread = upload_heartbeat.start_upload_heartbeat(
                self._client,
                task.execution_id,
                task.lease_id,
                task.heartbeat_stop,
                self._heartbeat_interval,
            )
        try:
            self._deliver(task)
        except Exception as exc:
            print(f"upload task crashed for {task.execution_id}: {exc}", flush=True)
        finally:
            upload_heartbeat.quiesce_heartbeat(task.heartbeat_stop, task.heartbeat_thread, 2)
            self._status.finish(task.execution_id)
            with self._lock:
                self._depth -= 1

    def _prepare(self, task: UploadTask) -> tuple[dict[str, Any], Path, list[str]]:
        """Build (metadata, archive, output names); may raise — caller degrades
        to a failed-result report, mirroring the old inline catch-all."""
        archive = task.execution_dir / "result.tar.gz"
        if task.kind == "prebuilt":
            metadata = dict(task.prebuilt_metadata or {})
            metadata.setdefault("output_artifacts", {})
            _write_empty_archive(archive)
            return metadata, archive, []
        job_dir = task.execution_dir / "job"
        run_dir = job_dir / "runs" / task.node_key / "worker"
        events = run_dir / "events.jsonl"
        # Pi exits 0 even when the model call fails (e.g. provider 401); one
        # pass folds the model-error scan into the compression rewrite.
        if task.exit_code == 0:
            model_error, _, _ = scan_and_compress_pi_events(events)
        else:
            model_error = None
            compress_pi_events(events)
        outputs = [
            name for name in task.expected_outputs if (job_dir / PurePosixPath(name)).is_file()
        ]
        if task.exit_code == 130:
            result_status, error = "cancelled", "Agent Worker is shutting down"
        elif task.exit_code == 0:
            if model_error:
                result_status, error = "failed", model_error
            else:
                result_status, error = "completed", ""
        else:
            result_status, error = "failed", f"Agent process exited {task.exit_code}"
        metadata: dict[str, Any] = {
            "status": result_status,
            "exit_code": task.exit_code,
            "error_message": error,
            "command": list(task.command),
            "output_artifacts": {},
            "run_dir": PurePosixPath(run_dir.relative_to(job_dir)).as_posix(),
        }
        with tarfile.open(archive, "w:gz") as tar:
            for name in outputs:
                tar.add(job_dir / PurePosixPath(name), arcname=name)
            tar.add(run_dir, arcname=str(run_dir.relative_to(job_dir)))
        return metadata, archive, outputs

    def _deliver(self, task: UploadTask) -> None:
        if self._stop.is_set():
            return  # never started; marker intact for the next startup
        self._status.set_phase(task.execution_id, "uploading")
        job_dir = task.execution_dir / "job"
        try:
            metadata, archive, outputs = self._prepare(task)
        except Exception as exc:
            metadata = {
                "status": "failed",
                "exit_code": 1,
                "error_message": f"result preparation failed: {exc}"[:MAX_ERROR_MESSAGE_CHARS],
                "command": list(task.command),
                "output_artifacts": {},
            }
            archive = task.execution_dir / "result.tar.gz"
            _write_empty_archive(archive)
            outputs = []
        uploaded: dict[str, str] = {}
        for name in outputs:
            try:
                ref = self._upload_with_retry(job_dir / PurePosixPath(name))
            except HostRequestError as exc:
                # Terminal 4xx on the artifact itself: report the run failed
                # instead of looping forever on a verdict that cannot change.
                metadata = {
                    "status": "failed",
                    "exit_code": 1,
                    "error_message": str(exc)[:MAX_ERROR_MESSAGE_CHARS],
                    "command": list(task.command),
                    "output_artifacts": {},
                }
                uploaded = {}
                break
            if ref is None:
                return  # shutting down mid-upload; marker stays for restore
            uploaded[name] = ref
        metadata["output_artifacts"] = uploaded
        # Quiesce the heartbeat before the final report: a beat racing the
        # commit loses the row lock and logs a spurious "lost ownership" 409.
        # Resume only while a transient report failure backs off.
        # Deliberately NOT run_with_retry: each backoff window must re-arm the
        # lease heartbeat, which the shared plain-sleep loop cannot express.
        upload_heartbeat.quiesce_task_heartbeat(task, _HEARTBEAT_JOIN_SECONDS)
        backoff = _RETRY_BASE_SECONDS
        while not self._stop.is_set():
            try:
                status_code, body = self._client.report(
                    task.execution_id, task.lease_id, metadata, archive
                )
            except RuntimeError as exc:
                print(
                    f"result report retry for {task.execution_id}: {exc}",
                    flush=True,
                )
                # An unbounded backoff chain can outlive the lease TTL.
                task.heartbeat_stop = threading.Event()
                task.heartbeat_thread = upload_heartbeat.start_upload_heartbeat(
                    self._client,
                    task.execution_id,
                    task.lease_id,
                    task.heartbeat_stop,
                    self._heartbeat_interval,
                )
                self._stop.wait(backoff)
                backoff = min(backoff * 2, _RETRY_CAP_SECONDS)
                upload_heartbeat.quiesce_task_heartbeat(task, _HEARTBEAT_JOIN_SECONDS)
                continue
            if status_code == 204:
                break
            # 409: lease gone (Host swept/requeued) — the result is moot.
            # Other 4xx: the Host rejected the payload itself; keep the log,
            # drop the result, never retry a verdict.
            print(
                f"result report rejected for {task.execution_id}:"
                f" HTTP {status_code}: {body[:200]!r}",
                flush=True,
            )
            break
        else:
            return  # stopped before the report resolved; marker stays
        marker = task.execution_dir / PENDING_FILENAME
        marker.unlink(missing_ok=True)
        shutil.rmtree(task.execution_dir, ignore_errors=True)

    def _upload_with_retry(self, path: Path) -> str | None:
        """Upload one artifact; None = stopped (retry next startup); 4xx propagates."""
        return run_with_retry(
            lambda: self._client.upload_artifact(path),
            retriable=(RuntimeError,),
            terminal=(HostRequestError,),
            base_seconds=_RETRY_BASE_SECONDS,
            cap_seconds=_RETRY_CAP_SECONDS,
            stop=self._stop,
            on_retry=lambda exc, _backoff: print(
                f"artifact upload retry for {path.name}: {exc}", flush=True
            ),
        )
