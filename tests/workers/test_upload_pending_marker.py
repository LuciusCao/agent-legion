"""Regression tests for the atomic upload-pending marker (worker/upload/queue.py).

The marker is the Durability anchor of the upload queue: a crash that leaves
a half-written JSON marker makes restore() rmtree a finished execution dir.
submit() must therefore write it atomically, complete or absent, never partial.
Field roundtrip is part of the same anchor: whatever the crash-restore path
needs (``max_archive_bytes`` since #1174 F1) must survive to_json/from_json,
and older markers without the field keep the documented default.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from worker.status import ExecutionStatusReporter
from worker.upload.queue import PENDING_FILENAME, UploadQueue, UploadTask

pytestmark = pytest.mark.no_db


class _FakeClient:
    def report(self, execution_id: str, lease_id: str, archive: Path):
        return 204, b""

    def heartbeat(self, execution_id: str, lease_id: str) -> tuple[int, list[str]]:
        return 204, []


def _task(work_root: Path, execution_id: str = "exec-1") -> UploadTask:
    return UploadTask(
        execution_id=execution_id,
        lease_id="lease-1",
        execution_dir=work_root / execution_id,
        node_key="node_a",
        status_fields={"node_key": "node_a"},
        kind="prebuilt",
        prebuilt_metadata={"status": "failed", "exit_code": 1, "error_message": "x"},
    )


def _stopped_queue() -> UploadQueue:
    stop = threading.Event()
    stop.set()  # deliver 立即 bail，marker 原样留在盘上
    return UploadQueue(
        _FakeClient(),
        ExecutionStatusReporter(None),
        heartbeat_interval=3600,
        stop=stop,
    )


def test_submit_writes_complete_parseable_marker(tmp_path: Path) -> None:
    work_root = tmp_path / "work"
    (work_root / "exec-1").mkdir(parents=True)
    queue = _stopped_queue()
    task = _task(work_root)

    queue.submit(task)
    queue.shutdown()

    marker = work_root / "exec-1" / PENDING_FILENAME
    assert json.loads(marker.read_text(encoding="utf-8")) == task.to_json()
    # 原子写不留同目录临时文件。
    assert [p.name for p in (work_root / "exec-1").iterdir()] == [PENDING_FILENAME]


def test_submit_failed_write_leaves_no_partial_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_root = tmp_path / "work"
    (work_root / "exec-1").mkdir(parents=True)

    def _crash_replace(src: str, dst: str) -> None:
        raise OSError("simulated crash mid-write")

    monkeypatch.setattr(os, "replace", _crash_replace)
    queue = _stopped_queue()
    with pytest.raises(OSError, match="simulated crash"):
        queue.submit(_task(work_root))
    queue.shutdown()

    execution_dir = work_root / "exec-1"
    assert not (execution_dir / PENDING_FILENAME).exists()
    assert list(execution_dir.iterdir()) == []  # 临时文件也被清理


def test_marker_roundtrip_preserves_max_archive_bytes(tmp_path: Path) -> None:
    """#1174 F1（持久化层）：claim 下发的 ``max_archive_bytes`` 随 marker 往返
    ——修复前 ``from_json`` 不读该字段，崩溃恢复的任务恒 0。#1184 复审起
    该值只作诊断/观测锚点：往返同时置 ``max_archive_bytes_restored``
    （预检与回收不再信任它——快照双向可过期，矩阵见 report_policy）。"""
    work_root = tmp_path / "work"
    (work_root / "exec-1").mkdir(parents=True)
    task = _task(work_root)
    task.max_archive_bytes = 2048

    restored = UploadTask.from_json(
        json.loads(json.dumps(task.to_json(), ensure_ascii=False)), work_root
    )

    assert task.to_json()["max_archive_bytes"] == 2048
    assert restored.max_archive_bytes == 2048
    assert restored.max_archive_bytes_restored is True  # 恢复值 = 不可信快照
    assert task.max_archive_bytes_restored is False  # 在线构造缺省 = 可信


def test_marker_without_max_archive_bytes_defaults_to_zero(tmp_path: Path) -> None:
    """旧版本 marker（#1174 之前落盘、无该字段）兼容：归 0——预检不猜、
    413 回收臂按协议下限裁剪（两处 0 值语义矩阵见 report_policy docstring）。
    恢复标记同样置位（无字段的 0 与有字段的过期值同属「不可信快照」）。"""
    work_root = tmp_path / "work"
    (work_root / "exec-1").mkdir(parents=True)
    payload = _task(work_root).to_json()
    del payload["max_archive_bytes"]

    restored = UploadTask.from_json(payload, work_root)

    assert restored.max_archive_bytes == 0
    assert restored.max_archive_bytes_restored is True


def test_restore_discards_truncated_marker(tmp_path: Path) -> None:
    """崩溃截断的 marker（合法 JSON 前缀）必须被丢弃，不得重放半截任务。"""
    work_root = tmp_path / "work"
    (work_root / "exec-1").mkdir(parents=True)
    complete = json.dumps(_task(work_root).to_json(), ensure_ascii=False)
    (work_root / "exec-1" / PENDING_FILENAME).write_text(
        complete[: len(complete) // 2], encoding="utf-8"
    )
    queue = _stopped_queue()

    assert queue.restore(work_root) == 0
    queue.shutdown()

    assert not (work_root / "exec-1").exists()
