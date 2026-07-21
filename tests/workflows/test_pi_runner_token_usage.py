from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from server.app.jobs import JobQueries
from server.app.workflows.pi_config import PiConfig
from server.app.workflows.pi_runner import PiRunner
from tests.postgres_support import TEST_DATABASE_URL


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    skill_dir = tmp_path / "skills" / "test_skill"
    skill_dir.mkdir(parents=True)
    return skill_dir


def _setup_job(job_db: JobQueries) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (?, ?)",
            ("ws-1", "Test"),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id) "
            "values (?, ?, ?, ?, ?)",
            ("job-1", "ws-1", "wf", "question", "q-1"),
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key, status) values (?, ?, ?)",
            ("job-1", "review_keywords", "pending"),
        )


def _fake_popen_class(outputs: list[str], events: list[dict]):
    class FakePopen:
        def __init__(
            self,
            command: list[str],
            stdout=None,
            stderr=None,
            cwd: str = "",
            env: dict | None = None,
            start_new_session: bool = False,
            **kwargs: object,
        ) -> None:
            self.returncode = 0
            for event in events:
                stdout.write(json.dumps(event) + "\n")
            stdout.flush()
            stderr.write("ok")
            for out in outputs:
                (Path(cwd) / out).write_text("{}", encoding="utf-8")

        def poll(self) -> int:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

    return FakePopen


def test_pi_runner_captures_token_usage(skill_dir: Path, tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    jobs_dir = data_dir / "jobs"
    job_dir = jobs_dir / "ws-1" / "job-1"
    job_dir.mkdir(parents=True)
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir)
    _setup_job(job_db)

    config = PiConfig(binary="pi", provider="gateway", model="your-model-a")
    outputs = ["out.json"]
    events = [
        {
            "type": "message_end",
            "message": {"usage": {"input": 10, "output": 5, "cacheRead": 1}},
        },
    ]

    monkeypatch.setattr(subprocess, "Popen", _fake_popen_class(outputs, events))

    runner = PiRunner(config, skill_root=skill_dir.parent)
    result = runner.run(
        job={"id": "job-1", "workspace_id": "ws-1"},
        node_key="review_keywords",
        skill_dir=skill_dir,
        inputs=[],
        outputs=outputs,
        job_db=job_db,
        job_dir=job_dir,
        jobs_dir=jobs_dir,
    )

    assert result.status == "completed"

    with job_db.connect() as conn:
        row = conn.execute("select * from node_run_token_usage").fetchone()

    assert row is not None
    assert row["input_tokens"] == 10
    assert row["output_tokens"] == 5
    assert row["cache_read_tokens"] == 1
    assert row["total_tokens"] == 16
    assert row["message_count"] == 1
    assert row["provider"] == "gateway"
    assert row["model"] == "your-model-a"
    assert row["workspace_id"] == "ws-1"


def test_pi_runner_token_usage_failure_does_not_fail_run(
    skill_dir: Path, tmp_path: Path, monkeypatch
):
    data_dir = tmp_path / "data"
    jobs_dir = data_dir / "jobs"
    job_dir = jobs_dir / "ws-1" / "job-1"
    job_dir.mkdir(parents=True)
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir)
    _setup_job(job_db)

    config = PiConfig(binary="pi", provider="gateway", model="your-model-a")
    outputs = ["out.json"]
    events = [
        {
            "type": "message_end",
            "message": {"usage": {"input": 10, "output": 5, "cacheRead": 1}},
        },
    ]

    monkeypatch.setattr(subprocess, "Popen", _fake_popen_class(outputs, events))

    def _exploding_parse(_run_dir, _node_run):
        raise RuntimeError("boom")

    monkeypatch.setattr("server.app.services.token_usage_capture.parse_run_usage", _exploding_parse)

    runner = PiRunner(config, skill_root=skill_dir.parent)
    result = runner.run(
        job={"id": "job-1", "workspace_id": "ws-1"},
        node_key="review_keywords",
        skill_dir=skill_dir,
        inputs=[],
        outputs=outputs,
        job_db=job_db,
        job_dir=job_dir,
        jobs_dir=jobs_dir,
    )

    assert result.status == "completed"

    with job_db.connect() as conn:
        row = conn.execute("select * from node_run_token_usage").fetchone()

    assert row is None
