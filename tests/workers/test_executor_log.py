"""Unit tests for the executor rolling log sink (#566 phase 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from worker.executor_log import ExecutorLogSink, executor_log_path

pytestmark = pytest.mark.no_db


def test_log_path_anchors_to_worker_data_domain(tmp_path: Path) -> None:
    data_state = tmp_path / "data" / "agent-worker-service"
    assert executor_log_path(data_state) == tmp_path / "data" / "logs" / "executor.log"
    custom = tmp_path / "elsewhere" / "worker-state"
    assert executor_log_path(custom) == custom / "logs" / "executor.log"


def test_sink_writes_and_rotates(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "executor.log"
    sink = ExecutorLogSink(path, max_bytes=200, backups=2)
    errors: list[str] = []
    for index in range(20):
        sink.write(f"line {index:02d} " + "x" * 40, errors.append)
    sink.close()

    assert errors == []
    rotated = sorted(path.parent.glob("executor.log*"))
    # Rotation fired: current file plus bounded backups, never unbounded.
    assert len(rotated) >= 2
    assert len(rotated) <= 3  # current + 2 backups
    # The newest lines survive in the current file.
    assert "line 19" in path.read_text(encoding="utf-8")


def test_sink_failure_mutes_itself_and_reports_once(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "executor.log"
    sink = ExecutorLogSink(path, max_bytes=200, backups=1)
    errors: list[str] = []
    sink.write("ok", errors.append)
    assert errors == []
    # Break the handle: next write raises OSError underneath.
    assert sink._handle is not None
    sink._handle.close()

    sink.write("boom", errors.append)
    sink.write("boom again", errors.append)

    assert len(errors) == 1
    assert "滚动日志写入失败" in errors[0]
