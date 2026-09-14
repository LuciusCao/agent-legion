"""Unit tests for the executor rolling log sinks (#566 phase 3, #510)."""

from __future__ import annotations

from pathlib import Path

import pytest

from worker.executor_log import (
    EVENTS_BACKUP_COUNT,
    EVENTS_MAX_BYTES,
    ExecutorLogSink,
    events_log_path,
    executor_log_path,
    is_structured_event,
)
from worker.supervisor import WorkerSupervisor

pytestmark = pytest.mark.no_db


def test_log_path_anchors_to_worker_data_domain(tmp_path: Path) -> None:
    data_state = tmp_path / "data" / "agent-worker-service"
    assert (
        executor_log_path(data_state)
        == tmp_path / "data" / "logs" / "executor-agent-worker-service.log"
    )
    custom = tmp_path / "elsewhere" / "worker-state"
    assert executor_log_path(custom) == custom / "logs" / "executor.log"


def test_events_path_anchors_like_the_panel_log(tmp_path: Path) -> None:
    """#510: the events sink follows the same anchoring rule with the
    events- prefix and .jsonl extension."""
    data_state = tmp_path / "data" / "agent-worker-service"
    assert (
        events_log_path(data_state)
        == tmp_path / "data" / "logs" / "events-agent-worker-service.jsonl"
    )
    custom = tmp_path / "elsewhere" / "worker-state"
    assert events_log_path(custom) == custom / "logs" / "events.jsonl"
    # Tighter rotation budget than the panel log (see EVENTS_MAX_BYTES).
    assert EVENTS_MAX_BYTES < 10 * 1024 * 1024
    assert EVENTS_BACKUP_COUNT == 3


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


def test_structured_event_sniff_matches_worker_emit_shape() -> None:
    """#510: the supervisor's split predicate recognizes the exact shape
    worker.events.emit_event produces (sorted keys → event leads) and stays
    conservative on lookalikes (misclassification only moves one line
    between files, zero runtime effect)."""
    assert is_structured_event('{"event": "http.error", "ts": "2026-09-14"}')
    assert is_structured_event('{"event": "claim.attempt", "limit": 8}')
    assert not is_structured_event("[12:00:00] slots 3/64 (agent)")
    assert not is_structured_event('{"ts": "2026-09-14", "event": "x"}')
    assert not is_structured_event('{"event": "unterminated')


def test_supervisor_splits_events_into_their_own_sink(tmp_path: Path) -> None:
    """End-to-end through WorkerSupervisor._log: panel text lands only in the
    executor log (timestamped), structured events ALSO land in events.jsonl
    (untimestamped — the JSON body carries its own ts)."""
    from worker.config_store import WorkerConfigStore

    state = tmp_path / "data" / "agent-worker-service"
    state.mkdir(parents=True)
    store = WorkerConfigStore(state)
    supervisor = WorkerSupervisor(store, tmp_path / "worker.py")

    event_line = '{"event": "http.error", "status_code": 502, "ts": "2026-09-14T00:00:00"}'
    supervisor._log("slots 3/64 (agent) code 0/8")
    supervisor._log(event_line)
    supervisor._sinks.close()  # stop() 需要完整进程生命周期；直接收口 sink

    panel = executor_log_path(state).read_text(encoding="utf-8")
    events = events_log_path(state).read_text(encoding="utf-8")
    # Panel keeps every line (events included — the deque panel is unchanged).
    assert "slots 3/64" in panel
    assert "http.error" in panel
    # The events file carries ONLY the JSON line, without the panel prefix.
    assert events.splitlines() == [event_line]
