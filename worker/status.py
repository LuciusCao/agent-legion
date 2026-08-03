"""Volatile per-execution status shared from the executor process to the Worker Service.

The executor (child process) owns writes; the Worker Service (parent) polls reads.
The file is runtime state only: the Supervisor deletes it when the executor exits,
and readers treat a dead writer pid as "no current executions".
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker._atomic import atomic_write
from worker.status_reader import read_current_executions, read_runtime_status

ENV_VAR = "AGENT_WORKER_STATUS_FILE"
STATUS_FILENAME = "current_executions.json"

__all__ = [
    "ENV_VAR",
    "STATUS_FILENAME",
    "ExecutionStatusReporter",
    "read_current_executions",
    "read_runtime_status",
]


class ExecutionStatusReporter:
    """Track in-flight executions in one JSON file; thread-safe, best-effort writes."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._executions: dict[str, dict[str, Any]] = {}
        self._remote: dict[str, Any] = {}

    @classmethod
    def from_env(cls) -> ExecutionStatusReporter:
        raw = os.environ.get(ENV_VAR, "").strip()
        return cls(Path(raw) if raw else None)

    def start(self, execution_id: str, **fields: Any) -> None:
        entry = {
            "execution_id": execution_id,
            **fields,
            "phase": "claimed",
            "started_at": datetime.now(UTC).isoformat(),
        }
        with self._lock:
            self._executions[execution_id] = entry
            self._flush()

    def set_phase(self, execution_id: str, phase: str) -> None:
        with self._lock:
            if execution_id in self._executions:
                self._executions[execution_id]["phase"] = phase
                self._flush()

    def finish(self, execution_id: str) -> None:
        with self._lock:
            if self._executions.pop(execution_id, None) is not None:
                self._flush()

    def set_remote(self, remote: dict[str, Any]) -> None:
        """Publish the child process's Worker-token-authenticated Host view."""
        with self._lock:
            if remote != self._remote:
                self._remote = remote
                self._flush()

    def _flush(self) -> None:
        if self._path is None:
            return
        payload = {
            "pid": os.getpid(),
            "executions": self._executions,
            "remote": self._remote,
        }
        try:
            atomic_write(self._path, json.dumps(payload, ensure_ascii=False))
        except OSError as exc:  # 状态展示降级为"无当前执行"，不影响任务本身
            print(f"status file write failed: {exc}", flush=True)
