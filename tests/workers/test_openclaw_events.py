"""openclaw 一次性 envelope → pi 子集事件合成（worker/openclaw_events.py）。

实测形状（OpenClaw 2026.6.11 dist 源码 + docs/cli/agent.md）：--json 的
stdout 是一次性 pretty-printed envelope，诊断走 stderr；Worker 侧 stderr
合并进 stdout，捕获流 = 诊断行 + envelope。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared.pi_events import RELEVANT_EVENT_TYPES
from shared.pi_model_error import detect_model_error
from worker.openclaw_events import (
    _extract_envelope,
    envelope_to_events,
    synthesize_openclaw_events,
)

pytestmark = pytest.mark.no_db

ENVELOPE_PRETTY = """{
  "payloads": [
    {
      "text": "done",
      "mediaUrl": null
    }
  ],
  "meta": {
    "durationMs": 1200
  }
}"""


def test_extract_envelope_pretty_with_trailing_diagnostics() -> None:
    captured = f"[diagnostic] lane task start\n{ENVELOPE_PRETTY}\n[diagnostic] shutdown\n"
    envelope = _extract_envelope(captured)
    assert envelope is not None
    assert envelope["payloads"][0]["text"] == "done"


def test_extract_envelope_single_line() -> None:
    envelope = _extract_envelope('noise\n{"payloads": [], "meta": {}}\n')
    assert envelope == {"payloads": [], "meta": {}}


def test_extract_envelope_ignores_diagnostic_json_lines() -> None:
    captured = '{"error": {"message": "boom"}}\nplain text\n'
    assert _extract_envelope(captured) is None


def test_envelope_to_events_success_shape() -> None:
    events = envelope_to_events(
        {"payloads": [{"text": "done"}], "meta": {"durationMs": 1200}},
        session_id="sess",
        timestamp=100,
    )
    types = [event["type"] for event in events]
    assert types == [
        "session",
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    assert set(types) <= RELEVANT_EVENT_TYPES
    message = events[4]["message"]
    assert message["stopReason"] == "stop"
    assert message["content"] == [{"type": "text", "text": "done"}]
    assert events[0] == {"type": "session", "sessionId": "sess", "timestamp": 100}
    assert "error" not in events[-1]


def test_envelope_to_events_error_payload_feeds_model_error_detection(tmp_path: Path) -> None:
    events = envelope_to_events(
        {"payloads": [{"text": "401 invalid_authentication_error", "isError": True}]},
        session_id="sess",
        timestamp=100,
    )
    message = events[4]["message"]
    assert message["stopReason"] == "error"
    assert message["errorMessage"] == "401 invalid_authentication_error"
    assert events[-1] == {"type": "agent_end", "error": "401 invalid_authentication_error"}
    # detect_model_error 直接消费合成事件（Host 失败判定链）。
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    assert detect_model_error(path) == "401 invalid_authentication_error"


def test_envelope_to_events_meta_error_fallback() -> None:
    events = envelope_to_events(
        {"payloads": [], "meta": {"error": {"message": "model exploded"}}},
        session_id="sess",
        timestamp=100,
    )
    assert events[4]["message"]["errorMessage"] == "model exploded"


def test_synthesize_appends_and_preserves_raw_capture(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(f"[diag] start\n{ENVELOPE_PRETTY}\n", encoding="utf-8")
    synthesize_openclaw_events(path, session_id="exec-1", exit_code=0)
    lines = path.read_text(encoding="utf-8").splitlines()
    # 原始诊断保留（排障用），合成事件追加在尾部。
    assert lines[0] == "[diag] start"
    events = [json.loads(line) for line in lines if line.startswith('{"type": "')]
    assert events[-1] == {"type": "agent_end"}
    assert events[0]["sessionId"] == "exec-1"


def test_synthesize_cancelled_exit_without_envelope(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text("", encoding="utf-8")
    synthesize_openclaw_events(path, session_id="exec-1", exit_code=143)
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert events[-1] == {"type": "agent_end", "reason": "cancelled"}


def test_synthesize_failure_without_envelope_uses_diagnostic_tail(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text("FailoverError: LLM error invalid_authentication_error\n", encoding="utf-8")
    synthesize_openclaw_events(path, session_id="exec-1", exit_code=1)
    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith('{"type": "')
    ]
    assert events[-1]["type"] == "agent_end"
    assert "invalid_authentication_error" in events[-1]["error"]
    assert detect_model_error(path) is not None


def test_synthesize_exit_zero_without_envelope_is_empty_success(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text("weird but exited 0\n", encoding="utf-8")
    synthesize_openclaw_events(path, session_id="exec-1", exit_code=0)
    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith('{"type": "')
    ]
    assert events[-1] == {"type": "agent_end"}
    assert events[4]["message"]["stopReason"] == "stop"
