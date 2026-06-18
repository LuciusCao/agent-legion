import json
from pathlib import Path

from server.app.pipeline.openclaw_sessions import (
    _message_text,
    openclaw_sessions_dir,
    render_openclaw_session,
    resolve_openclaw_session_path,
)


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


def test_openclaw_sessions_dir_uses_agent_or_default() -> None:
    assert (
        openclaw_sessions_dir("my-agent")
        == Path.home() / ".openclaw" / "agents" / "my-agent" / "sessions"
    )
    assert openclaw_sessions_dir("") == Path.home() / ".openclaw" / "agents" / "main" / "sessions"


def test_resolve_openclaw_session_path_with_empty_session_id() -> None:
    assert resolve_openclaw_session_path("agent", "") is None


def test_resolve_openclaw_session_path_with_existing_file_path(tmp_path: Path) -> None:
    session_path = tmp_path / "session.jsonl"
    session_path.write_text("{}", encoding="utf-8")
    resolved = resolve_openclaw_session_path("agent", str(session_path))
    assert resolved == session_path


def test_resolve_openclaw_session_path_with_missing_file_path(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.jsonl"
    assert resolve_openclaw_session_path("agent", str(missing_path)) is None


def test_resolve_openclaw_session_path_exact_match(tmp_path: Path, monkeypatch) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    session_path = sessions_dir / "abc123.jsonl"
    session_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "server.app.pipeline.openclaw_sessions.openclaw_sessions_dir",
        lambda _agent_id: sessions_dir,
    )

    assert resolve_openclaw_session_path("agent", "abc123") == session_path


def test_resolve_openclaw_session_path_fuzzy_match(tmp_path: Path, monkeypatch) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    session_path = sessions_dir / "prefix-abc123-suffix.jsonl"
    session_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "server.app.pipeline.openclaw_sessions.openclaw_sessions_dir",
        lambda _agent_id: sessions_dir,
    )

    assert resolve_openclaw_session_path("agent", "abc123") == session_path


def test_resolve_openclaw_session_path_ambiguous_fuzzy_match(tmp_path: Path, monkeypatch) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "prefix-abc123.jsonl").write_text("{}", encoding="utf-8")
    (sessions_dir / "other-abc123.jsonl").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "server.app.pipeline.openclaw_sessions.openclaw_sessions_dir",
        lambda _agent_id: sessions_dir,
    )

    assert resolve_openclaw_session_path("agent", "abc123") is None


def test_resolve_openclaw_session_path_skips_trajectory_files(tmp_path: Path, monkeypatch) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    trajectory_path = sessions_dir / "abc123.trajectory.jsonl"
    trajectory_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "server.app.pipeline.openclaw_sessions.openclaw_sessions_dir",
        lambda _agent_id: sessions_dir,
    )

    assert resolve_openclaw_session_path("agent", "abc123") is None


def test_resolve_openclaw_session_path_missing_dir(tmp_path: Path, monkeypatch) -> None:
    sessions_dir = tmp_path / "sessions"
    monkeypatch.setattr(
        "server.app.pipeline.openclaw_sessions.openclaw_sessions_dir",
        lambda _agent_id: sessions_dir,
    )

    assert resolve_openclaw_session_path("agent", "abc123") is None


def test_message_text_extracts_text_and_tool_calls() -> None:
    message = {
        "content": [
            {"type": "text", "text": "hello"},
            {"type": "toolCall", "name": "read_file"},
            {"type": "image"},
            "not a dict",
        ]
    }
    text = _message_text(message)
    assert "hello" in text
    assert "[tool call] read_file" in text


def test_message_text_returns_empty_for_missing_content() -> None:
    assert _message_text({}) == ""
    assert _message_text({"content": None}) == ""


def test_render_openclaw_session_includes_session_header(tmp_path: Path) -> None:
    session_path = tmp_path / "test.jsonl"
    session_path.write_text(
        json.dumps({"type": "session", "id": "session-1", "cwd": "/tmp"})
        + "\n"
        + json.dumps({"type": "ignore", "data": "x"})
        + "\n"
        + json.dumps(
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "hi"}],
                },
            }
        )
        + "\n"
        + "not valid json\n",
        encoding="utf-8",
    )

    rendered = render_openclaw_session(session_path)

    assert "[SESSION] session-1 cwd=/tmp" in rendered
    assert "[ASSISTANT]" in rendered
    assert "hi" in rendered
    assert "ignore" not in rendered
