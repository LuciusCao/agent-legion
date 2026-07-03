import json

from server.app.services.job_log_renderer import _parse_pi_events
from server.app.services.pi_event_compression import compress_pi_events


def test_compress_pi_events_keeps_renderable_events(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(
        "\n".join(
            [
                '{"type":"session"}',
                '{"type":"agent_start"}',
                '{"type":"turn_start"}',
                '{"type":"message_start"}',
                '{"type":"message_update","assistantMessageEvent":{"type":"thinking_delta","delta":"hello"}}',
                '{"type":"message_update","assistantMessageEvent":{"type":"thinking_delta","delta":" world"}}',
                '{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"final"}]}}',
                '{"type":"tool_execution_start"}',
                '{"type":"tool_execution_update"}',
                '{"type":"tool_execution_end"}',
                '{"type":"agent_end"}',
            ]
        )
        + "\n"
    )

    original, compressed = compress_pi_events(events)
    assert original > compressed

    lines = events.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 8
    assert all(json.loads(line)["type"] != "message_update" for line in lines)

    entries = _parse_pi_events(events)
    assert any("回复" in entry["title"] for entry in entries)


def test_compress_pi_events_skips_missing_file(tmp_path):
    missing = tmp_path / "events.jsonl"
    assert compress_pi_events(missing) == (0, 0)


def test_compress_pi_events_handles_invalid_json(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text('{"type":"agent_start"}\nnot json\n{"type":"message_end"}\n')
    original, compressed = compress_pi_events(events)
    assert compressed > 0
    assert '"type":"agent_start"' in events.read_text()
    assert "not json" not in events.read_text()
