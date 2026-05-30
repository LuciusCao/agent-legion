import json
from pathlib import Path

from server.app.pipeline.openclaw_sessions import render_openclaw_session


def test_render_openclaw_session_keeps_last_n_lines(tmp_path: Path) -> None:
    session_path = tmp_path / "test.jsonl"
    lines = []
    for i in range(600):
        lines.append(
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": f"msg-{i}"}],
                    },
                }
            )
        )
    session_path.write_text("\n".join(lines), encoding="utf-8")

    rendered = render_openclaw_session(session_path)
    # Header + session info + at most 500 message lines
    assert "msg-599" in rendered
    assert "msg-0" not in rendered
    assert "Session file:" in rendered


def test_render_openclaw_session_truncates_by_char_limit(tmp_path: Path) -> None:
    session_path = tmp_path / "test.jsonl"
    long_text = "X" * 1000
    lines = []
    for _ in range(30):
        lines.append(
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": long_text}],
                    },
                }
            )
        )
    session_path.write_text("\n".join(lines), encoding="utf-8")

    rendered = render_openclaw_session(session_path, limit=5000)
    assert len(rendered) <= 5000
    # Should keep the tail
    assert "Session file:" not in rendered or len(rendered) == 5000
