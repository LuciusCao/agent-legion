from server.app.executors._log_utils import MAX_COPIED_LOG_BYTES, copy_pi_logs


def test_copy_pi_logs_combines_events_and_stderr(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text('{"type":"agent_start"}\n', encoding="utf-8")
    (run_dir / "stderr.log").write_text("error line\n", encoding="utf-8")
    log_path = tmp_path / "out.log"

    copy_pi_logs(run_dir, log_path)

    text = log_path.read_text(encoding="utf-8")
    assert "agent_start" in text
    assert "error line" in text


def test_copy_pi_logs_caps_size(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    events = run_dir / "events.jsonl"
    events.write_bytes(b"x" * (MAX_COPIED_LOG_BYTES + 1000))
    log_path = tmp_path / "out.log"

    copy_pi_logs(run_dir, log_path)

    data = log_path.read_bytes()
    assert len(data) <= MAX_COPIED_LOG_BYTES + 100
    assert b"truncated" in data
    assert data.endswith(b"x" * 100)


def test_copy_pi_logs_no_sources(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    log_path = tmp_path / "out.log"
    copy_pi_logs(run_dir, log_path)
    assert not log_path.exists()
