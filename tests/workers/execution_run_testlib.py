"""Shared fakes/builders for the run_execution lifecycle tests
(worker/execution/run.py), split from tests/workers/test_execution_run.py
when it crossed the 800-line test-file budget (#779 codex train review R3).
The sibling test files import these; cases migrated verbatim.
"""

from __future__ import annotations

import hashlib
import stat
import threading
from pathlib import Path
from typing import Any

from server.app.agent_broker.agent_bundle import build_agent_bundle
from worker.execution.run import run_execution
from worker.status import ExecutionStatusReporter
from worker.upload.queue import UploadQueue


def _make_bundle(tmp_path: Path, manifest: dict) -> Path:
    skill_src = tmp_path / "skill_src"
    skill_src.mkdir(exist_ok=True)
    (skill_src / "SKILL.md").write_text("# s", encoding="utf-8")
    bundle = tmp_path / f"bundle-{len(list(tmp_path.glob('bundle-*')))}.tar.gz"
    build_agent_bundle(bundle, skill_dir=skill_src, manifest=manifest)
    return bundle


def _manifest(command: list[str], *, timeout_seconds: int = 60) -> dict:
    return {
        "command_spec": {"command": command, "prompt": "do the thing"},
        "input_artifacts": {},
        "expected_outputs": ["output.json"],
        "execution": {"timeout_seconds": timeout_seconds},
    }


def _claim(execution_id: str = "exec-1") -> dict:
    return {
        "execution_id": execution_id,
        "lease_id": "lease-1",
        "node_key": "node_a",
        "bundle_url": "/api/agent-executions/exec-1/bundle",
    }


class FakeClient:
    """In-memory stand-in for agent_worker.Client."""

    def __init__(
        self,
        bundle: Path,
        *,
        heartbeat_status: int = 204,
        release_status: int = 204,
        batch_status: int = 200,
    ) -> None:
        self._bundle = bundle
        self._heartbeat_status = heartbeat_status
        self._release_status = release_status
        # #352: 批量心跳端点的状态码（200 正常；404/405 = pre-v5 Host，
        # 协调器降级为逐执行心跳）。
        self.batch_status = batch_status
        self.heartbeats = 0
        self.heartbeat_lease_ids: list[str] = []
        self.batch_calls: list[list[tuple[str, str]]] = []
        self.reports: list[dict] = []
        self.report_lease_ids: list[str] = []
        self.release_calls = 0
        self.uploads: dict[str, bytes] = {}

    def download(self, path: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self._bundle.read_bytes())

    def upload_artifact(self, path: Path) -> str:
        data = path.read_bytes()
        self.uploads[hashlib.sha256(data).hexdigest()] = data
        return f"sha256:{hashlib.sha256(data).hexdigest()}"

    def get_self(self) -> dict:
        return {
            "worker_id": "w1",
            "name": "Worker 1",
            "revoked": False,
            "online": True,
        }

    def heartbeat(
        self, execution_id: str, lease_id: str, timeout: float | None = None
    ) -> tuple[int, list[str]]:
        self.heartbeats += 1
        self.heartbeat_lease_ids.append(lease_id)
        return self._heartbeat_status, []

    def heartbeat_batch(
        self, executions: list[tuple[str, str]]
    ) -> tuple[int, dict[str, list[str]]] | None:
        self.batch_calls.append(list(executions))
        if self.batch_status in (404, 405):
            return None
        lost = (
            [execution_id for execution_id, _ in executions]
            if self._heartbeat_status in (401, 409)
            else []
        )
        renewed = [execution_id for execution_id, _ in executions if execution_id not in lost]
        return (
            self.batch_status,
            {"renewed": renewed, "lost": lost, "cancelled_execution_ids": []},
        )

    def release_slot(self, execution_id: str, lease_id: str) -> int:
        self.release_calls += 1
        return self._release_status

    def report(
        self, execution_id: str, lease_id: str, metadata: dict, archive: Path
    ) -> tuple[int, bytes]:
        self.reports.append(metadata)
        self.report_lease_ids.append(lease_id)
        return 204, b""


def _run(
    client: FakeClient,
    work_root: Path,
    shutdown: threading.Event | None = None,
    *,
    heartbeat_registry: Any | None = None,
) -> None:
    uploads = UploadQueue(
        client,
        ExecutionStatusReporter(None),
        max_concurrency=2,
        heartbeat_interval=0.05,
        stop=threading.Event(),
        heartbeat_registry=heartbeat_registry,
    )
    run_execution(
        client,
        _claim(),
        work_root,
        {},
        0.05,
        shutdown or threading.Event(),
        1,
        ExecutionStatusReporter(None),
        uploads,
        threading.Semaphore(4),
        heartbeat_registry,
    )
    # Uploads are asynchronous now; drain the queue before asserting.
    uploads.shutdown()


def _write_executable(path: Path, body: str) -> str:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)
