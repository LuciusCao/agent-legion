"""Report-lane priority and hot concurrency tests for the Worker upload queue.

The queue splits delivery into a bulk lane (prepare + artifact uploads) and a
strictly-prioritized report lane (quiesce heartbeat → report → drop marker).
These tests pin the lane ordering and the runtime concurrency adjustment;
heartbeat quiesce/resume and marker durability stay covered by
tests/test_worker_upload_queue.py and tests/workers/test_upload_pending_marker.py,
whose assertions exercise the same code after the split.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest

from tests.helpers import wait_for_predicate
from worker.status import ExecutionStatusReporter
from worker.upload_queue import UploadQueue, UploadTask

pytestmark = pytest.mark.no_db


class LaneFakeClient:
    """记录事件顺序的上传桩；首个 artifact 上传 park 在 gate 上制造积压。"""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.first_upload_entered = threading.Event()
        self.release_uploads = threading.Event()
        self._uploads = 0
        self._lock = threading.Lock()

    def upload_artifact(self, path: Path) -> str:
        with self._lock:
            self._uploads += 1
            first = self._uploads == 1
        if first:
            self.first_upload_entered.set()
            assert self.release_uploads.wait(10)
        self.events.append(f"upload:{path.name}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return f"sha256:{digest}"

    def report(
        self, execution_id: str, lease_id: str, metadata: dict, archive: Path
    ) -> tuple[int, bytes]:
        self.events.append(f"report:{execution_id}")
        return 204, b""

    def heartbeat(self, execution_id: str, lease_id: str) -> int:
        return 204


def _execution_dir(work_root: Path, execution_id: str, output_name: str) -> None:
    run_dir = work_root / execution_id / "job" / "runs" / "node_a" / "worker"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text(
        json.dumps({"type": "message_end", "message": {"role": "assistant"}}) + "\n",
        encoding="utf-8",
    )
    (work_root / execution_id / "job" / output_name).write_text("{}", encoding="utf-8")


def _task(work_root: Path, execution_id: str, output_name: str) -> UploadTask:
    return UploadTask(
        execution_id=execution_id,
        lease_id="lease-1",
        execution_dir=work_root / execution_id,
        node_key="node_a",
        status_fields={"node_key": "node_a"},
        kind="process",
        exit_code=0,
        expected_outputs=(output_name,),
        command=("pi",),
    )


def _queue(client: LaneFakeClient, max_concurrency: int = 1) -> UploadQueue:
    return UploadQueue(
        client,
        ExecutionStatusReporter(None),
        max_concurrency=max_concurrency,
        heartbeat_interval=0.05,
        stop=threading.Event(),
    )


def test_report_lane_preempts_bulk_backlog(tmp_path: Path) -> None:
    """唯一上传槽被 exec-1 的 bulk 占住、exec-2 排队时，exec-1 的 report
    必须插队到 exec-2 的 bulk 之前——小 payload 的 report 决定 finished_at
    与下游调度，不能被其它任务的 bulk 工作堵住。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root, "exec-1", "out-1.json")
    _execution_dir(work_root, "exec-2", "out-2.json")
    client = LaneFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root, "exec-1", "out-1.json"))
    assert client.first_upload_entered.wait(10)
    queue.submit(_task(work_root, "exec-2", "out-2.json"))
    assert queue.depth == 2

    client.release_uploads.set()
    queue.shutdown()

    assert client.events == [
        "upload:out-1.json",
        "report:exec-1",
        "upload:out-2.json",
        "report:exec-2",
    ]
    # report 成功后 marker 与执行目录都被清掉。
    assert not (work_root / "exec-1").exists()
    assert not (work_root / "exec-2").exists()
    assert queue.depth == 0


def test_set_max_concurrency_backfills_pending_uploads(tmp_path: Path) -> None:
    """热调大并发立即补位：exec-2 的整条 bulk+report 在 exec-1 仍 park 时完成。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root, "exec-1", "out-1.json")
    _execution_dir(work_root, "exec-2", "out-2.json")
    client = LaneFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root, "exec-1", "out-1.json"))
    assert client.first_upload_entered.wait(10)
    queue.submit(_task(work_root, "exec-2", "out-2.json"))
    # limit=1：exec-2 只能排队，不会动到它的 artifact。
    assert not any(event.startswith("upload:out-2") for event in client.events)

    queue.set_max_concurrency(2)

    wait_for_predicate(lambda: "report:exec-2" in client.events, timeout=10)
    # exec-1 仍 park 在上传 gate 上（其事件要等 release 后才记录），exec-2 已全程走完。
    assert client.events == ["upload:out-2.json", "report:exec-2"]
    client.release_uploads.set()
    queue.shutdown()
    assert client.events[-2:] == ["upload:out-1.json", "report:exec-1"]
    assert queue.depth == 0
