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


def test_scan_and_compress_matches_separate_calls(tmp_path):
    from server.app.services.pi_event_compression import scan_and_compress_pi_events
    from server.app.workflows.pi_protocol import detect_model_error

    payload = "\n".join(
        [
            '{"type":"session"}',
            '{"type":"message_start","message":{"role":"assistant"}}',
            '{"type":"message_update","assistantMessageEvent":{"type":"text_delta"}}',
            '{"type":"message_end","message":{"role":"assistant","stopReason":"toolUse"}}',
            '{"type":"message_end","message":{"role":"assistant","stopReason":"error","errorMessage":"terminated"}}',
            '{"type":"message_end","message":{"role":"assistant","stopReason":"stop"}}',
            '{"type":"tool_execution_end"}',
        ]
    )

    separate = tmp_path / "separate.jsonl"
    separate.write_text(payload + "\n")
    expected_error = detect_model_error(separate)
    compress_pi_events(separate)

    combined = tmp_path / "combined.jsonl"
    combined.write_text(payload + "\n")
    model_error, original, compressed = scan_and_compress_pi_events(combined)

    assert model_error == expected_error is None
    assert original == len(payload) + 1
    assert compressed == combined.stat().st_size
    assert combined.read_text() == separate.read_text()


def test_scan_and_compress_reports_unrecovered_error(tmp_path):
    from server.app.services.pi_event_compression import scan_and_compress_pi_events

    events = tmp_path / "events.jsonl"
    events.write_text(
        '{"type":"message_end","message":{"role":"assistant","stopReason":"error","errorMessage":"400 bad request"}}\n'
    )
    model_error, original, compressed = scan_and_compress_pi_events(events)
    assert model_error == "400 bad request"
    assert original > 0 and compressed > 0


def test_scan_and_compress_skips_missing_file(tmp_path):
    from server.app.services.pi_event_compression import scan_and_compress_pi_events

    assert scan_and_compress_pi_events(tmp_path / "missing.jsonl") == (None, 0, 0)


def test_scan_and_compress_velites_retry_stream_judged_recovered(tmp_path):
    # velites retry pattern (same as Node Pi): each failed transient attempt
    # emits an error message_end + auto_retry_start; the later successful
    # message_end clears the error, so the run is judged recovered — and
    # auto_retry_start must survive compression (pi_event_scan allowlist).
    from server.app.services.pi_event_compression import scan_and_compress_pi_events

    events = tmp_path / "events.jsonl"
    events.write_text(
        "\n".join(
            [
                '{"type":"session","sessionId":"s1"}',
                '{"type":"message_start","message":{"role":"assistant"}}',
                '{"type":"message_end","message":{"role":"assistant","stopReason":"error","errorMessage":"provider call failed (transient): terminated"}}',
                '{"type":"auto_retry_start","attempt":1,"maxAttempts":4,"delayMs":1000,"error":"terminated"}',
                '{"type":"message_end","message":{"role":"assistant","stopReason":"stop","usage":{"input":1,"output":1,"cacheRead":0}}}',
                '{"type":"agent_end"}',
            ]
        )
        + "\n"
    )
    model_error, original, compressed = scan_and_compress_pi_events(events)
    assert model_error is None, "recovered retry must not be judged a model failure"
    assert original > 0 and compressed > 0

    kept_types = [
        json.loads(line)["type"] for line in events.read_text(encoding="utf-8").strip().splitlines()
    ]
    assert "auto_retry_start" in kept_types
    assert kept_types == [
        "session",
        "message_start",
        "message_end",
        "auto_retry_start",
        "message_end",
        "agent_end",
    ]
