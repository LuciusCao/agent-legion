"""Tests for ``worker.event_filter.pump_filtered_events``."""

from __future__ import annotations

import io

from worker.event_filter import pump_filtered_events


def _pump(lines: list[str]) -> str:
    src = io.BytesIO(("\n".join(lines) + "\n").encode())
    dst = io.BytesIO()
    pump_filtered_events(src, dst)
    return dst.getvalue().decode()


def test_pump_drops_message_update_deltas() -> None:
    out = _pump(
        [
            '{"type":"message_start"}',
            '{"type":"message_update","assistantMessageEvent":{"type":"thinking_delta"}}',
            '{"type":"message_update","assistantMessageEvent":{"type":"text_delta"}}',
            '{"type":"message_end","message":{"role":"assistant"}}',
        ]
    )
    kept = out.strip().splitlines()
    assert kept == [
        '{"type":"message_start"}',
        '{"type":"message_end","message":{"role":"assistant"}}',
    ]


def test_pump_keeps_unknown_and_future_event_types() -> None:
    # Denylist semantics: only known delta spam is dropped; anything else —
    # including event types this Worker version does not know — passes
    # through so error signals can never be filtered out accidentally.
    out = _pump(
        [
            '{"type":"turn_start"}',
            '{"type":"tool_execution_update","partial":{}}',
            '{"type":"some_future_event"}',
            '{"type":"tool_execution_end"}',
        ]
    )
    assert out.strip().splitlines() == [
        '{"type":"turn_start"}',
        '{"type":"some_future_event"}',
        '{"type":"tool_execution_end"}',
    ]


def test_pump_keeps_non_json_lines_verbatim() -> None:
    out = _pump(
        [
            '{"type":"agent_start"}',
            "Traceback (most recent call last):",
            "  File x, line 1",
            '{"type":"agent_end"}',
        ]
    )
    assert "Traceback (most recent call last):" in out
    assert "  File x, line 1" in out


def test_pump_skips_blank_lines() -> None:
    out = _pump(['{"type":"agent_start"}', "", "   ", '{"type":"agent_end"}'])
    assert out.strip().splitlines() == ['{"type":"agent_start"}', '{"type":"agent_end"}']


def test_pump_handles_none_source() -> None:
    dst = io.BytesIO()
    pump_filtered_events(None, dst)
    assert dst.getvalue() == b""


def test_pump_tolerates_closed_destination() -> None:
    src = io.BytesIO(b'{"type":"agent_start"}\n{"type":"agent_end"}\n')
    dst = io.BytesIO()
    dst.close()
    pump_filtered_events(src, dst)  # must not raise
