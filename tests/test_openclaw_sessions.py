from unittest.mock import patch

from server.app.pipeline.openclaw_sessions import (
    render_openclaw_session,
    resolve_openclaw_session_path,
)


def test_resolve_empty_session_id_returns_none():
    assert resolve_openclaw_session_path("main", "") is None


def test_resolve_absolute_path_returns_path_if_exists(tmp_path):
    session_file = tmp_path / "my_session.jsonl"
    session_file.write_text("")
    result = resolve_openclaw_session_path("main", str(session_file))
    assert result == session_file


def test_resolve_absolute_path_returns_none_if_not_exists(tmp_path):
    assert resolve_openclaw_session_path("main", str(tmp_path / "nonexistent.jsonl")) is None


def test_resolve_with_jsonl_suffix_in_input(tmp_path):
    """Input ending with .jsonl should be treated as absolute path."""
    session_file = tmp_path / "foo.jsonl"
    session_file.write_text("")
    result = resolve_openclaw_session_path("main", str(session_file))
    assert result == session_file


def test_resolve_exact_match(tmp_path):
    sessions_dir = tmp_path / ".openclaw" / "agents" / "main" / "sessions"
    sessions_dir.mkdir(parents=True)
    session_file = sessions_dir / "abc123.jsonl"
    session_file.write_text("")

    with patch(
        "server.app.pipeline.openclaw_sessions.openclaw_sessions_dir",
        return_value=sessions_dir,
    ):
        assert resolve_openclaw_session_path("main", "abc123") == session_file


def test_resolve_fuzzy_match_single_result(tmp_path):
    sessions_dir = tmp_path / ".openclaw" / "agents" / "main" / "sessions"
    sessions_dir.mkdir(parents=True)
    session_file = sessions_dir / "abc123-def.jsonl"
    session_file.write_text("")

    with patch(
        "server.app.pipeline.openclaw_sessions.openclaw_sessions_dir",
        return_value=sessions_dir,
    ):
        assert resolve_openclaw_session_path("main", "abc123") == session_file


def test_resolve_fuzzy_match_multiple_returns_none(tmp_path):
    sessions_dir = tmp_path / ".openclaw" / "agents" / "main" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "abc123-def.jsonl").write_text("")
    (sessions_dir / "abc123-ghi.jsonl").write_text("")

    with patch(
        "server.app.pipeline.openclaw_sessions.openclaw_sessions_dir",
        return_value=sessions_dir,
    ):
        assert resolve_openclaw_session_path("main", "abc123") is None


def test_resolve_skips_trajectory_files(tmp_path):
    sessions_dir = tmp_path / ".openclaw" / "agents" / "main" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "abc123.trajectory.jsonl").write_text("")

    with patch(
        "server.app.pipeline.openclaw_sessions.openclaw_sessions_dir",
        return_value=sessions_dir,
    ):
        assert resolve_openclaw_session_path("main", "abc123") is None


def test_resolve_no_sessions_dir_returns_none(tmp_path):
    sessions_dir = tmp_path / ".openclaw" / "agents" / "main" / "sessions"
    # do not create directory
    with patch(
        "server.app.pipeline.openclaw_sessions.openclaw_sessions_dir",
        return_value=sessions_dir,
    ):
        assert resolve_openclaw_session_path("main", "abc123") is None


# --- render_openclaw_session ---


def test_render_session_extracts_messages(tmp_path):
    session_file = tmp_path / "session.jsonl"
    session_file.write_text(
        '{"type": "session", "id": "s1", "cwd": "/tmp"}\n'
        '{"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]}}\n'
        '{"type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": "world"}]}}\n',
        encoding="utf-8",
    )
    result = render_openclaw_session(session_file)
    assert "[SESSION] s1 cwd=/tmp" in result
    assert "[USER]\nhello" in result
    assert "[ASSISTANT]\nworld" in result


def test_render_session_ignores_invalid_json(tmp_path):
    session_file = tmp_path / "session.jsonl"
    session_file.write_text(
        "not json\n"
        '{"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "ok"}]}}\n',
        encoding="utf-8",
    )
    result = render_openclaw_session(session_file)
    assert "[USER]\nok" in result
    assert "not json" not in result


def test_render_session_includes_tool_calls(tmp_path):
    session_file = tmp_path / "session.jsonl"
    session_file.write_text(
        '{"type": "message", "message": {"role": "assistant", "content": [{"type": "toolCall", "name": "read_file"}]}}\n',
        encoding="utf-8",
    )
    result = render_openclaw_session(session_file)
    assert "[tool call] read_file" in result


def test_render_session_trims_to_limit(tmp_path):
    session_file = tmp_path / "session.jsonl"
    long_text = "x" * 10000
    session_file.write_text(
        '{"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "'
        + long_text
        + '"}]}}\n',
        encoding="utf-8",
    )
    result = render_openclaw_session(session_file, limit=100)
    assert len(result) <= 100


def test_render_session_skips_empty_text(tmp_path):
    session_file = tmp_path / "session.jsonl"
    session_file.write_text(
        '{"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "   "}]}}\n',
        encoding="utf-8",
    )
    result = render_openclaw_session(session_file)
    assert "[USER]" not in result


def test_render_session_skips_non_dict_content_items(tmp_path):
    session_file = tmp_path / "session.jsonl"
    session_file.write_text(
        '{"type": "message", "message": {"role": "user", "content": ["not a dict", {"type": "text", "text": "keep"}]}}\n',
        encoding="utf-8",
    )
    result = render_openclaw_session(session_file)
    assert "keep" in result
    assert "not a dict" not in result


def test_render_session_handles_non_dict_message(tmp_path):
    session_file = tmp_path / "session.jsonl"
    session_file.write_text(
        '{"type": "message", "message": "not a dict"}\n',
        encoding="utf-8",
    )
    result = render_openclaw_session(session_file)
    assert "Session file:" in result


def test_render_session_with_empty_content(tmp_path):
    session_file = tmp_path / "session.jsonl"
    session_file.write_text(
        '{"type": "message", "message": {"role": "user", "content": []}}\n',
        encoding="utf-8",
    )
    result = render_openclaw_session(session_file)
    assert "[USER]" not in result
