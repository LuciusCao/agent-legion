from pathlib import Path

import pytest

from server.app.services.job_errors import NotFoundError
from server.app.services.job_log_raw import read_raw_log


class _JobDbStub:
    def __init__(self, run) -> None:
        self._run = run

    def get_node_run(self, job_id: str, run_id: int):
        return self._run


def test_read_raw_log_rejects_missing_run(settings) -> None:
    with pytest.raises(NotFoundError, match="Run not found"):
        read_raw_log("job-1", 1, _JobDbStub(None), settings)


def test_read_raw_log_returns_empty_without_log_path(settings) -> None:
    run = {"log_path": "", "run_dir": "", "node_key": "node-a", "job_id": "job-1"}

    assert read_raw_log("job-1", 1, _JobDbStub(run), settings) == ""


def test_read_raw_log_reads_events_from_run_dir_when_log_file_missing(settings) -> None:
    run_dir = settings.jobs_dir / "ws1" / "job-1" / "runs" / "node-a" / "token-1"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text('{"type": "agent_start"}\n', encoding="utf-8")
    run = {
        "log_path": str(settings.logs_dir / "jobs" / "job-1-node-a.log"),
        "run_dir": str(run_dir),
        "node_key": "node-a",
        "job_id": "job-1",
    }

    raw = read_raw_log("job-1", 1, _JobDbStub(run), settings)

    assert "agent_start" in raw


def test_read_raw_log_returns_empty_when_run_dir_has_no_events(settings, tmp_path: Path) -> None:
    run_dir = settings.jobs_dir / "ws1" / "job-1" / "runs" / "node-a" / "token-1"
    run_dir.mkdir(parents=True)
    run = {
        "log_path": str(settings.logs_dir / "jobs" / "job-1-node-a.log"),
        "run_dir": str(run_dir),
        "node_key": "node-a",
        "job_id": "job-1",
    }

    assert read_raw_log("job-1", 1, _JobDbStub(run), settings) == ""


def test_read_raw_log_derives_run_dir_from_legacy_log_path(settings) -> None:
    token_dir = settings.jobs_dir / "ws1" / "job-1" / "runs" / "node-a" / "token-1"
    token_dir.mkdir(parents=True)
    (token_dir / "events.jsonl").write_text('{"type": "message"}\n', encoding="utf-8")
    run = {
        "log_path": str(settings.logs_dir / "jobs" / "job-1-node-a.log"),
        "run_dir": "",
        "node_key": "node-a",
        "job_id": "job-1",
    }

    raw = read_raw_log("job-1", 1, _JobDbStub(run), settings)

    assert "message" in raw


def test_read_raw_log_returns_empty_when_nothing_resolves(settings) -> None:
    run = {
        "log_path": str(settings.logs_dir / "jobs" / "job-1-node-a.log"),
        "run_dir": "",
        "node_key": "",
        "job_id": "job-1",
    }

    assert read_raw_log("job-1", 1, _JobDbStub(run), settings) == ""
