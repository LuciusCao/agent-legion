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


def _emit_event_line(emit_fn) -> str:
    """Capture one real worker.events emit_* line from stdout (the contract
    the sniff must match — hand-written JSON drifted once already, R1 P1)."""
    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        emit_fn()
    return buffer.getvalue().strip()


def test_structured_event_sniff_matches_real_worker_emissions() -> None:
    """#510 review R1 P1：嗅探必须认得 emit_event 的真实产物——
    sort_keys 下多数事件的关键载荷键（body/error/agent_budget/...）
    排在 "event" 之前，前缀检查漏掉的恰是 http.error 等取证事件。"""
    from worker import events

    # 每个事件族一条真实发射线（覆盖载荷键排序在 event 前后的两种形态）。
    real_lines = [
        _emit_event_line(
            lambda: events.note_http_error_response(
                "http://h", "/api/agent-executions/claim", 502, b"bad gateway"
            )
        ),
        _emit_event_line(lambda: events.note_claim_backoff("w1", RuntimeError("x"), 2.0, 3)),
        _emit_event_line(
            lambda: events.note_execution_failed(
                {
                    "execution_id": "e",
                    "job_id": "j",
                    "workspace_id": "w",
                    "node_key": "n",
                    "kind": "agent",
                },
                RuntimeError("boom"),
                1.0,
            )
        ),
        _emit_event_line(
            lambda: events.emit_event("claim.attempt", {"worker_id": "w1", "limit": 8})
        ),
    ]
    for line in real_lines:
        assert line.startswith("{") and line.endswith("}"), line
        assert is_structured_event(line), f"real emission escaped the sniff: {line}"

    # 保守面：面板节奏文本与非事件 JSON 不进 events.jsonl。
    assert not is_structured_event("[12:00:00] slots 3/64 (agent)")
    assert not is_structured_event('{"ts": "2026-09-14", "other": true}')
    assert not is_structured_event('{"event": "unterminated')


def test_supervisor_splits_events_into_their_own_sink(tmp_path: Path) -> None:
    """End-to-end through WorkerSupervisor._log: panel text lands only in the
    executor log (timestamped), structured events ALSO land in events.jsonl
    (untimestamped — the JSON body carries its own ts)."""
    from worker import events
    from worker.config_store import WorkerConfigStore

    state = tmp_path / "data" / "agent-worker-service"
    state.mkdir(parents=True)
    store = WorkerConfigStore(state)
    supervisor = WorkerSupervisor(store, tmp_path / "worker.py")

    # 用真实发射线驱动（R1 P1：手写 JSON 曾与 emit_event 形态漂移——
    # 该事件的载荷键 body/status_code 排序在 event 之前，恰是前缀嗅探
    # 漏掉的那类）。
    event_line = _emit_event_line(
        lambda: events.note_http_error_response(
            "http://h", "/api/agent-executions/claim", 502, b"bad gateway"
        )
    )
    supervisor._log("slots 3/64 (agent) code 0/8")
    supervisor._log(event_line)
    supervisor._sinks.close()  # stop() 需要完整进程生命周期；直接收口 sink

    panel = executor_log_path(state).read_text(encoding="utf-8")
    events_body = events_log_path(state).read_text(encoding="utf-8")
    # Panel keeps every line (events included — the deque panel is unchanged).
    assert "slots 3/64" in panel
    assert "http.error" in panel
    # The events file carries ONLY the JSON line, without the panel prefix.
    assert events_body.splitlines() == [event_line]
