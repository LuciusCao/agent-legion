from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from server.app.jobs import JobQueries
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.job_log_paths import resolve_job_log_path, resolve_run_dir
from server.app.services.job_log_raw import MAX_RAW_LOG_BYTES, PayloadTooLargeError
from server.app.services.job_logs import JobLogService
from server.app.services.vault import VaultService
from server.app.settings import Settings
from server.app.storage_paths import make_data_relative
from tests.postgres_support import TEST_DATABASE_URL


def _create_job_with_run(
    job_db: JobQueries, settings: Settings, log_path: str | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    workspace = job_db.create_workspace(
        "Test WS", default_workflow_key="question_comprehension_info"
    )
    job = job_db.create_job(
        workflow_key="question_comprehension_info",
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
    db_path = TEST_DATABASE_URL
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
    db_path = TEST_DATABASE_URL
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

    job, run = _create_job_with_run(
        job_db, settings, make_data_relative(log_file, settings.data_dir)
    )
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

    job, run = _create_job_with_run(
        job_db, settings, make_data_relative(log_file, settings.data_dir)
    )
    result = service.read(job["id"], run["id"])

    assert result["truncated"] is True
    assert len(result["log"].encode("utf-8")) <= 8 * 1024 + 100


def test_job_log_service_returns_empty_for_missing_file(log_service):
    service, settings, job_db = log_service
    logs_root = settings.logs_dir / "jobs"
    logs_root.mkdir(parents=True, exist_ok=True)
    log_file = logs_root / "missing.log"

    job, run = _create_job_with_run(
        job_db, settings, make_data_relative(log_file, settings.data_dir)
    )
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

    workspace = job_db.create_workspace(
        "Other WS", default_workflow_key="question_comprehension_info"
    )
    other_job = job_db.create_job(
        workflow_key="question_comprehension_info",
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
        make_data_relative(log_file, settings.data_dir),
    )

    with pytest.raises(NotFoundError, match="Run not found"):
        service.read("default_question_comprehension_info_Q001", run["id"])


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


def test_job_log_service_rejects_wrong_category_inside_data_dir(log_service):
    service, settings, job_db = log_service
    videos_dir = settings.videos_dir / "v1"
    videos_dir.mkdir(parents=True, exist_ok=True)
    log_file = videos_dir / "phase.log"
    log_file.write_text("secret", encoding="utf-8")

    job, run = _create_job_with_run(
        job_db, settings, make_data_relative(log_file, settings.data_dir)
    )
    with pytest.raises(InvalidOperationError, match="Invalid log path"):
        service.read(job["id"], run["id"])


def test_job_log_service_rejects_symlink_escape(log_service, tmp_path):
    service, settings, job_db = log_service
    outside = tmp_path / "outside.log"
    outside.write_text("secret", encoding="utf-8")
    linked = settings.logs_dir / "jobs" / "linked.log"
    linked.parent.mkdir(parents=True, exist_ok=True)
    linked.symlink_to(outside)

    job, run = _create_job_with_run(job_db, settings, make_data_relative(linked, settings.data_dir))
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

    job, run = _create_job_with_run(
        job_db, settings, make_data_relative(log_file, settings.data_dir)
    )
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

    job, run = _create_job_with_run(
        job_db, settings, make_data_relative(log_file, settings.data_dir)
    )
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

    job, run = _create_job_with_run(
        job_db, settings, make_data_relative(log_file, settings.data_dir)
    )
    result = service.read(job["id"], run["id"])

    assert "cms-token-123" not in result["log"]
    assert "cms-password" not in result["log"]
    assert "openclaw-key" not in result["log"]
    assert "keep-visible" in result["log"]
    assert "<redacted>" in result["log"]


def test_job_log_service_redacts_vault_secrets(log_service, monkeypatch):
    service, settings, job_db = log_service
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    logs_root = settings.logs_dir / "jobs"
    logs_root.mkdir(parents=True, exist_ok=True)
    log_file = logs_root / "run.log"
    log_file.write_text("vault-plain-value keep-visible", encoding="utf-8")
    job, run = _create_job_with_run(
        job_db, settings, make_data_relative(log_file, settings.data_dir)
    )
    vault = VaultService(job_db.path, settings.config)
    vault.set(str(job["workspace_id"]), "cms-token", "vault-plain-value")

    result = service.read(job["id"], run["id"])

    assert "vault-plain-value" not in result["log"]
    assert "keep-visible" in result["log"]
    assert "<redacted>" in result["log"]


def test_job_log_service_preserves_empty_config_values(log_service):
    service, settings, job_db = log_service
    settings.config = {"token": ""}
    logs_root = settings.logs_dir / "jobs"
    logs_root.mkdir(parents=True, exist_ok=True)
    log_file = logs_root / "run.log"
    log_file.write_text("nothing to hide", encoding="utf-8")

    job, run = _create_job_with_run(
        job_db, settings, make_data_relative(log_file, settings.data_dir)
    )
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

    job, run = _create_job_with_run(
        job_db, settings, make_data_relative(log_file, settings.data_dir)
    )
    result = service.read(job["id"], run["id"])

    assert "hidden-token" not in result["log"]
    assert "hidden-key" not in result["log"]
    assert "visible-value" in result["log"]


def _write_events(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events),
        encoding="utf-8",
    )


def _create_pi_job(
    job_db: JobQueries,
    settings: Settings,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    workspace = job_db.create_workspace("Pi WS", default_workflow_key="question_comprehension_info")
    job = job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q100",
        batch_id="batch-pi",
        title="Question Q100",
        node_keys=["generate_key_info"],
        workspace_id=workspace["id"],
    )
    run_dir = settings.jobs_dir / job["id"] / "runs" / "generate_key_info" / "r1"
    run_dir.mkdir(parents=True, exist_ok=True)
    log_file = run_dir / "events.jsonl"
    run = job_db.start_node_run(
        job["id"],
        "generate_key_info",
        ["pi", "--mode", "json"],
        make_data_relative(log_file, settings.data_dir),
        run_dir=str(run_dir),
    )
    assert run is not None
    return job, run, run_dir


def test_job_log_service_renders_pi_structured_events(log_service):
    service, settings, job_db = log_service
    job, run, run_dir = _create_pi_job(job_db, settings)
    log_file = run_dir / "events.jsonl"

    _write_events(
        log_file,
        [
            {"type": "agent_start"},
            {"type": "turn_start"},
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "Need to fetch context."},
                        {
                            "type": "toolCall",
                            "id": "tool_001",
                            "name": "fetch_context",
                            "arguments": {"question_id": "Q100"},
                        },
                    ],
                    "stopReason": "toolUse",
                },
            },
            {
                "type": "message_end",
                "message": {
                    "role": "toolResult",
                    "toolCallId": "tool_001",
                    "toolName": "fetch_context",
                    "content": [{"type": "text", "text": "context body"}],
                    "isError": False,
                },
            },
            {"type": "turn_start"},
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Done."}],
                    "stopReason": "stop",
                },
            },
        ],
    )

    result = service.read(job["id"], run["id"])

    assert result["run_id"] == run["id"]
    assert result["truncated"] is False
    types = [entry["type"] for entry in result["structured"]]
    assert types == ["session", "thinking", "tool_call", "tool_result", "message"]
    assert any(entry["title"].startswith("Turn 1") for entry in result["structured"])
    assert any(entry["title"].startswith("Turn 2") for entry in result["structured"])
    assert result["raw_url"] == f"/api/jobs/{job['id']}/runs/{run['id']}/log?raw=1"


def test_job_log_service_includes_sanitized_command_and_prompt_in_agent_start(log_service):
    service, settings, job_db = log_service
    job, run, run_dir = _create_pi_job(job_db, settings)
    (run_dir / "prompt.md").write_text(
        f"Read {settings.root_dir}/input.json with token123",
        encoding="utf-8",
    )
    _write_events(run_dir / "events.jsonl", [{"type": "agent_start"}])

    result = service.read(job["id"], run["id"])

    start = result["structured"][0]
    assert start["title"] == "Agent 开始运行"
    assert "启动命令\npi --mode json" in start["detail"]
    assert "提示词\nRead <local-path>/input.json with <redacted>" in start["detail"]
    assert str(settings.root_dir) not in start["detail"]
    assert "token123" not in start["detail"]


def test_job_log_service_does_not_truncate_structured_entry_details(log_service):
    service, settings, job_db = log_service
    job, run, run_dir = _create_pi_job(job_db, settings)
    long_detail = "x" * 1200
    _write_events(
        run_dir / "events.jsonl",
        [
            {"type": "turn_start"},
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "thinking", "thinking": long_detail}],
                    "stopReason": "stop",
                },
            },
        ],
    )

    result = service.read(job["id"], run["id"])

    assert result["structured"][0]["detail"] == long_detail
    assert result["structured"][0]["truncated"] is False
    assert "已截断" not in result["log"]


def test_job_log_service_renders_pi_error_stop_reason(log_service):
    service, settings, job_db = log_service
    job, run, run_dir = _create_pi_job(job_db, settings)
    log_file = run_dir / "events.jsonl"

    _write_events(
        log_file,
        [
            {"type": "turn_start"},
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [],
                    "stopReason": "error",
                    "errorMessage": "model refused",
                },
            },
        ],
    )

    result = service.read(job["id"], run["id"])

    assert any(entry["type"] == "error" for entry in result["structured"])
    assert "model refused" in result["log"]


def test_job_log_service_renders_pi_stderr(log_service):
    service, settings, job_db = log_service
    job, run, run_dir = _create_pi_job(job_db, settings)
    log_file = run_dir / "events.jsonl"
    stderr_file = run_dir / "stderr.log"
    stderr_file.write_text("warning line\n", encoding="utf-8")

    _write_events(
        log_file,
        [{"type": "message_end", "message": {"role": "assistant", "content": []}}],
    )

    result = service.read(job["id"], run["id"])

    assert any(entry["type"] == "stderr" for entry in result["structured"])
    assert "warning line" in result["log"]


def test_read_raw_log_falls_back_to_events_jsonl(log_service):
    service, settings, job_db = log_service
    job, run, run_dir = _create_pi_job(job_db, settings)
    events_path = run_dir / "events.jsonl"
    events_path.write_text(
        json.dumps({"type": "agent_start"}, ensure_ascii=False),
        encoding="utf-8",
    )

    raw = service.read_raw(job["id"], run["id"])

    assert "agent_start" in raw


def test_job_log_service_falls_back_to_events_jsonl_when_log_file_is_missing(
    log_service,
):
    service, settings, job_db = log_service
    job, run, run_dir = _create_pi_job(job_db, settings)
    missing_log = settings.logs_dir / "jobs" / "missing-pi.log"
    with job_db.connect() as conn:
        conn.execute(
            "update node_runs set log_path=%s where id=%s",
            (make_data_relative(missing_log, settings.data_dir), run["id"]),
        )
    _write_events(
        run_dir / "events.jsonl",
        [
            {"type": "agent_start"},
            {"type": "turn_start"},
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Agent result"}],
                    "stopReason": "stop",
                },
            },
        ],
    )

    result = service.read(job["id"], run["id"])

    assert result["run_id"] == run["id"]
    assert [entry["type"] for entry in result["structured"]] == ["session", "message"]
    assert "Agent result" in result["log"]
    assert result["raw_url"] == f"/api/jobs/{job['id']}/runs/{run['id']}/log?raw=1"


def test_resolve_job_log_path_accepts_logs_dir(log_service):
    _, settings, _ = log_service
    log_path = str(make_data_relative(settings.logs_dir / "jobs" / "test.log", settings.data_dir))
    resolved = resolve_job_log_path(log_path, settings)
    assert resolved == settings.logs_dir / "jobs" / "test.log"


def test_resolve_job_log_path_accepts_jobs_dir(log_service):
    _, settings, _ = log_service
    log_path = str(
        make_data_relative(
            settings.jobs_dir / "job-1" / "runs" / "node" / "events.jsonl", settings.data_dir
        )
    )
    resolved = resolve_job_log_path(log_path, settings)
    assert resolved == settings.jobs_dir / "job-1" / "runs" / "node" / "events.jsonl"


def test_resolve_job_log_path_rejects_empty_path(log_service):
    _, settings, _ = log_service
    with pytest.raises(InvalidOperationError, match="Empty log path"):
        resolve_job_log_path("", settings)


def test_resolve_job_log_path_rejects_outside_roots(log_service):
    _, settings, _ = log_service
    with pytest.raises(InvalidOperationError, match="Invalid log path"):
        resolve_job_log_path("/etc/passwd", settings)


def test_resolve_job_log_path_rejects_symlink_escape(log_service):
    _, settings, _ = log_service
    with pytest.raises(InvalidOperationError, match="Invalid log path"):
        resolve_job_log_path("videos/../../etc/passwd", settings)


def test_resolve_run_dir_returns_none_for_invalid_path(log_service):
    _, settings, _ = log_service
    assert resolve_run_dir("/not/inside/data", settings) is None


def test_resolve_run_dir_returns_existing_directory(log_service):
    _, settings, _ = log_service
    run_dir = settings.jobs_dir / "job-1" / "runs" / "node"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_dir_str = str(make_data_relative(run_dir, settings.data_dir))
    assert resolve_run_dir(run_dir_str, settings) == run_dir


def test_read_raw_log_rejects_oversized_file(log_service):
    service, settings, job_db = log_service
    job, run = _create_job_with_run(job_db, settings)
    log_file = settings.logs_dir / "jobs" / "test.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_bytes(b"x" * (MAX_RAW_LOG_BYTES + 1))

    # Point the existing run record at the oversized log file.
    with job_db.connect() as conn:
        conn.execute(
            "update node_runs set log_path=%s where id=%s",
            (str(make_data_relative(log_file, settings.data_dir)), run["id"]),
        )

    with pytest.raises(PayloadTooLargeError):
        service.read_raw(job["id"], run["id"])
