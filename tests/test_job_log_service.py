from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from server.app.jobs import JobQueries
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.job_logs import JobLogService
from server.app.settings import Settings


def _create_job_with_run(
    job_db: JobQueries, settings: Settings, log_path: str | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    workspace = job_db.create_workspace("Test WS")
    job = job_db.create_job(
        workflow_key="question_content",
        source_type="question",
        source_id="Q001",
        batch_id="batch-1",
        title="Question Q001",
        node_keys=["fetch_question_context"],
        workspace_id=workspace["id"],
    )
    run = job_db.start_node_run(
        job["id"],
        "fetch_question_context",
        ["echo", "hi"],
        log_path or "",
    )
    assert run is not None
    return job, run


@pytest.fixture
def log_service(tmp_path: Path) -> tuple[JobLogService, Settings, JobQueries]:
    db_path = tmp_path / "jobs.sqlite"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        root_dir=tmp_path,
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=logs_dir,
        packages_dir=tmp_path / "packages",
        jobs_dir=jobs_dir,
        config={"secret_key": "super-secret", "api_token": "token123"},
    )
    job_db = JobQueries(db_path, jobs_dir)
    return JobLogService(settings, job_db), settings, job_db


@pytest.fixture
def log_service_with_secret_config(
    tmp_path: Path,
) -> tuple[JobLogService, Settings, JobQueries]:
    db_path = tmp_path / "jobs.sqlite"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        root_dir=tmp_path,
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=logs_dir,
        packages_dir=tmp_path / "packages",
        jobs_dir=jobs_dir,
        config={
            "cms": {
                "token": "cms-token-123",
                "password": "cms-password",
            },
            "openclaw": {
                "api_key": "openclaw-key",
            },
        },
    )
    job_db = JobQueries(db_path, jobs_dir)
    return JobLogService(settings, job_db), settings, job_db


def test_job_log_service_returns_tail(log_service):
    service, settings, job_db = log_service
    logs_root = settings.logs_dir / "jobs"
    logs_root.mkdir(parents=True, exist_ok=True)
    log_file = logs_root / "run.log"
    log_file.write_text("line1\nline2\nline3\n", encoding="utf-8")

    job, run = _create_job_with_run(job_db, settings, str(log_file))
    result = service.read(job["id"], run["id"])

    assert result["run_id"] == run["id"]
    assert "line3" in result["log"]
    assert result["truncated"] is False


def test_job_log_service_truncates_long_file(log_service):
    service, settings, job_db = log_service
    logs_root = settings.logs_dir / "jobs"
    logs_root.mkdir(parents=True, exist_ok=True)
    log_file = logs_root / "run.log"
    # Create content larger than 12 KiB tail read limit
    chunk = "x" * 100 + "\n"
    repeats = (14 * 1024 // len(chunk)) + 10
    log_file.write_text(chunk * repeats, encoding="utf-8")

    job, run = _create_job_with_run(job_db, settings, str(log_file))
    result = service.read(job["id"], run["id"])

    assert result["truncated"] is True
    assert len(result["log"].encode("utf-8")) <= 8 * 1024 + 100


def test_job_log_service_returns_empty_for_missing_file(log_service):
    service, settings, job_db = log_service
    logs_root = settings.logs_dir / "jobs"
    logs_root.mkdir(parents=True, exist_ok=True)
    log_file = logs_root / "missing.log"

    job, run = _create_job_with_run(job_db, settings, str(log_file))
    result = service.read(job["id"], run["id"])

    assert result["run_id"] == run["id"]
    assert result["log"] == ""
    assert result["truncated"] is False


def test_job_log_service_rejects_run_from_other_job(log_service):
    service, settings, job_db = log_service
    logs_root = settings.logs_dir / "jobs"
    logs_root.mkdir(parents=True, exist_ok=True)
    log_file = logs_root / "run.log"
    log_file.write_text("secret", encoding="utf-8")

    workspace = job_db.create_workspace("Other WS")
    other_job = job_db.create_job(
        workflow_key="question_content",
        source_type="question",
        source_id="Q002",
        batch_id="batch-2",
        title="Question Q002",
        node_keys=["fetch_question_context"],
        workspace_id=workspace["id"],
    )
    run = job_db.start_node_run(
        other_job["id"],
        "fetch_question_context",
        ["echo", "hi"],
        str(log_file),
    )

    with pytest.raises(NotFoundError, match="Run not found"):
        service.read("default_question_content_Q001", run["id"])


def test_job_log_service_rejects_dotdot_escape(log_service):
    service, settings, job_db = log_service
    logs_root = settings.logs_dir / "jobs"
    logs_root.mkdir(parents=True, exist_ok=True)

    job, run = _create_job_with_run(job_db, settings, "../outside.log")
    with pytest.raises(InvalidOperationError, match="Invalid log path"):
        service.read(job["id"], run["id"])


def test_job_log_service_rejects_absolute_path(log_service):
    service, settings, job_db = log_service
    logs_root = settings.logs_dir / "jobs"
    logs_root.mkdir(parents=True, exist_ok=True)

    job, run = _create_job_with_run(job_db, settings, "/etc/passwd")
    with pytest.raises(InvalidOperationError, match="Invalid log path"):
        service.read(job["id"], run["id"])


def test_job_log_service_rejects_symlink_escape(log_service, tmp_path):
    service, settings, job_db = log_service
    outside = tmp_path / "outside.log"
    outside.write_text("secret", encoding="utf-8")
    linked = settings.logs_dir / "jobs" / "linked.log"
    linked.parent.mkdir(parents=True, exist_ok=True)
    linked.symlink_to(outside)

    job, run = _create_job_with_run(job_db, settings, str(linked))
    with pytest.raises(InvalidOperationError, match="Invalid log path"):
        service.read(job["id"], run["id"])


def test_job_log_service_redacts_home_path(log_service, tmp_path, monkeypatch):
    service, settings, job_db = log_service
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    logs_root = settings.logs_dir / "jobs"
    logs_root.mkdir(parents=True, exist_ok=True)
    log_file = logs_root / "run.log"
    log_file.write_text(f"loaded from {fake_home / '.config' / 'secret.ini'}", encoding="utf-8")

    job, run = _create_job_with_run(job_db, settings, str(log_file))
    result = service.read(job["id"], run["id"])

    assert str(fake_home) not in result["log"]
    assert "<local-path>" in result["log"]


def test_job_log_service_redacts_root_dir(log_service, tmp_path):
    service, settings, job_db = log_service
    logs_root = settings.logs_dir / "jobs"
    logs_root.mkdir(parents=True, exist_ok=True)
    log_file = logs_root / "run.log"
    log_file.write_text(
        f"config at {settings.root_dir / 'config' / 'workflow.yaml'}",
        encoding="utf-8",
    )

    job, run = _create_job_with_run(job_db, settings, str(log_file))
    result = service.read(job["id"], run["id"])

    assert str(settings.root_dir) not in result["log"]
    assert "<local-path>" in result["log"]


def test_job_log_service_redacts_config_secrets(log_service_with_secret_config):
    service, settings, job_db = log_service_with_secret_config
    logs_root = settings.logs_dir / "jobs"
    logs_root.mkdir(parents=True, exist_ok=True)
    log_file = logs_root / "run.log"
    log_file.write_text(
        "cms-token-123 cms-password openclaw-key keep-visible",
        encoding="utf-8",
    )

    job, run = _create_job_with_run(job_db, settings, str(log_file))
    result = service.read(job["id"], run["id"])

    assert "cms-token-123" not in result["log"]
    assert "cms-password" not in result["log"]
    assert "openclaw-key" not in result["log"]
    assert "keep-visible" in result["log"]
    assert "<redacted>" in result["log"]


def test_job_log_service_preserves_empty_config_values(log_service):
    service, settings, job_db = log_service
    settings.config = {"token": ""}
    logs_root = settings.logs_dir / "jobs"
    logs_root.mkdir(parents=True, exist_ok=True)
    log_file = logs_root / "run.log"
    log_file.write_text("nothing to hide", encoding="utf-8")

    job, run = _create_job_with_run(job_db, settings, str(log_file))
    result = service.read(job["id"], run["id"])

    assert result["log"] == "nothing to hide"


def test_job_log_service_redacts_nested_secret_values(log_service):
    service, settings, job_db = log_service
    settings.config = {
        "nested": {
            "secret_token": "hidden-token",
            "api_key_nested": "hidden-key",
        }
    }
    logs_root = settings.logs_dir / "jobs"
    logs_root.mkdir(parents=True, exist_ok=True)
    log_file = logs_root / "run.log"
    log_file.write_text("hidden-token hidden-key visible-value", encoding="utf-8")

    job, run = _create_job_with_run(job_db, settings, str(log_file))
    result = service.read(job["id"], run["id"])

    assert "hidden-token" not in result["log"]
    assert "hidden-key" not in result["log"]
    assert "visible-value" in result["log"]
