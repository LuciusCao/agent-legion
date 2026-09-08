"""execution.reported 事件（#551）：上传任务的分段耗时与结局。

每个上传任务 finalize 时发射一条 execution.reported：queue_wait /
prepare / transfer / report_wait / report 各段墙钟 + outcome +
archive_bytes。上传管线曾是供给-消费链上最后的黑盒（积压时无法回答
「卡在哪段」）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.helpers import wait_for_predicate
from worker.status import ExecutionStatusReporter
from worker.upload.queue import UploadQueue, UploadTask

pytestmark = pytest.mark.no_db


class _FakeClient:
    def __init__(self, report_status: int = 204) -> None:
        self.report_status = report_status

    def upload_artifact(self, path: Path) -> str:
        return "sha256:0" * 1

    def report(self, execution_id, lease_id, metadata, archive):  # type: ignore[no-untyped-def]
        return self.report_status, b""

    def heartbeat(self, execution_id, lease_id):  # type: ignore[no-untyped-def]
        return 204, []


def _seed_execution(work_root: Path, execution_id: str) -> None:
    run_dir = work_root / execution_id / "job" / "runs" / "node_a" / "worker"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
    (work_root / execution_id / "job" / "output.json").write_text("{}", encoding="utf-8")


def _task(work_root: Path, execution_id: str) -> UploadTask:
    return UploadTask(
        execution_id=execution_id,
        lease_id="lease-1",
        execution_dir=work_root / execution_id,
        node_key="node_a",
        status_fields={},
        kind="process",
        exit_code=0,
        expected_outputs=("output.json",),
    )


def _reported_events(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if '"execution.reported"' in line]


def test_reported_event_carries_stage_spans(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_execution(tmp_path, "exec-1")
    queue = UploadQueue(_FakeClient(), ExecutionStatusReporter(None), heartbeat_interval=0.05)
    queue.submit(_task(tmp_path, "exec-1"))
    wait_for_predicate(lambda: queue.depth == 0, timeout=10)
    queue.shutdown()

    events = _reported_events(capsys)
    assert len(events) == 1
    event = events[0]
    assert event["execution_id"] == "exec-1"
    assert event["outcome"] == "delivered"
    assert event["archive_bytes"] > 0
    for span in ("queue_wait", "prepare", "transfer", "report_wait", "report"):
        assert f"{span}_seconds" in event, f"missing span {span}: {event}"
        assert event[f"{span}_seconds"] >= 0


def test_reported_event_marks_rejection(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Host 409（租约已被重发）→ outcome=rejected——重复执行的观测锚点。"""
    _seed_execution(tmp_path, "exec-1")
    queue = UploadQueue(
        _FakeClient(report_status=409), ExecutionStatusReporter(None), heartbeat_interval=0.05
    )
    queue.submit(_task(tmp_path, "exec-1"))
    wait_for_predicate(lambda: queue.depth == 0, timeout=10)
    queue.shutdown()

    events = _reported_events(capsys)
    assert len(events) == 1
    assert events[0]["outcome"] == "rejected"


def test_queue_wait_grows_under_lane_pressure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """并发 1 + 首任务 park：后续任务的 queue_wait 必须反映排队时间。"""
    import threading

    class ParkingClient(_FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.gate = threading.Event()

        def upload_artifact(self, path: Path) -> str:
            self.gate.wait(10)
            return super().upload_artifact(path)

    client = ParkingClient()
    _seed_execution(tmp_path, "exec-1")
    _seed_execution(tmp_path, "exec-2")
    queue = UploadQueue(
        client, ExecutionStatusReporter(None), max_concurrency=1, heartbeat_interval=0.05
    )
    queue.submit(_task(tmp_path, "exec-1"))
    time.sleep(0.3)  # exec-1 占住唯一车道
    queue.submit(_task(tmp_path, "exec-2"))
    time.sleep(0.3)  # exec-2 在队列里等——gate 放行前它进不了车道
    client.gate.set()
    wait_for_predicate(lambda: queue.depth == 0, timeout=10)
    queue.shutdown()

    events = {e["execution_id"]: e for e in _reported_events(capsys)}
    assert events["exec-2"]["queue_wait_seconds"] > events["exec-1"]["queue_wait_seconds"] + 0.1


def test_reported_event_without_timer_omits_spans(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """中止/畸形路径（无计时器）：事件只剩基础字段，跨度键省略——
    never-raise 纪律（复审 P2：mark 的 None 守卫的对偶）。"""
    from types import SimpleNamespace

    from worker.upload.report_events import note_execution_reported

    task = SimpleNamespace(
        report_timer=None,
        execution_id="exec-x",
        node_key="node_a",
        exec_kind="agent",
        prepared_archive=None,
        execution_dir=tmp_path / "nonexistent",
    )
    note_execution_reported(task, "aborted")
    (event,) = _reported_events(capsys)
    assert event["outcome"] == "aborted"
    assert event["archive_bytes"] is None
    assert not any(key.endswith("_seconds") for key in event)
