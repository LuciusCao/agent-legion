"""job 日志把单次输出触顶渲染为可行动告警（#952）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.app.services.job_log_renderer import _parse_pi_events

pytestmark = pytest.mark.no_db


def _entries(tmp_path: Path, message: dict) -> list[dict]:
    events = tmp_path / "events.jsonl"
    lines = [{"type": "turn_start"}, {"type": "message_end", "message": message}]
    events.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return [dict(entry) for entry in _parse_pi_events(events)]


def test_length_stop_renders_output_limit_warning(tmp_path: Path) -> None:
    entries = _entries(
        tmp_path,
        {
            "role": "assistant",
            "stopReason": "length",
            "content": [{"type": "thinking", "thinking": "long..."}],
        },
    )
    warning = entries[-1]
    assert warning["type"] == "error"
    assert warning["title"] == "Turn 1 · 单次输出触顶"
    assert "thinking 计入同一预算" in warning["detail"]
    assert "max_output_tokens" in warning["detail"]


def test_length_stop_warning_also_covers_context_window_overflow(tmp_path: Path) -> None:
    """velites 把 Anthropic `model_context_window_exceeded` 也映射为 length，渲染文案须
    与 Worker 失败原因一致，同时提示上下文窗口溢出与缩短输入，而非只建议调高输出上限。"""
    entries = _entries(tmp_path, {"role": "assistant", "stopReason": "length"})
    detail = entries[-1]["detail"]
    assert "上下文窗口" in detail
    assert "缩短输入" in detail


def test_length_stop_with_error_message_keeps_model_error_entry(tmp_path: Path) -> None:
    entries = _entries(
        tmp_path, {"role": "assistant", "stopReason": "length", "errorMessage": "boom"}
    )
    assert entries[-1]["title"] == "Turn 1 · 模型调用错误"
    assert entries[-1]["detail"] == "boom"


def test_other_abnormal_stop_still_model_error(tmp_path: Path) -> None:
    entries = _entries(tmp_path, {"role": "assistant", "stopReason": "aborted"})
    assert entries[-1]["title"] == "Turn 1 · 模型调用错误"
    assert entries[-1]["detail"] == "stop_reason=aborted"
