from __future__ import annotations

import json
from pathlib import Path

from server.app.jobs import JobQueries
from server.app.workflows.definition import (
    load_workflow_definition,
)
from server.app.workflows.executor import (
    execute_node_once,
)
from server.app.workflows.pi_runner import PiRunner
from tests.workers.helpers import _make_fake_skill


def test_execute_local_node_run_persists_relative_log_path(tmp_path):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    workspace = queries.create_workspace("test_ws")
    definition = load_workflow_definition(Path("config/workflows/question_content.yaml"))
    job = queries.create_job(
        workflow_key="question_content",
        source_type="question_id",
        source_id="Q200",
        batch_id="",
        title="Question Q200",
        node_keys=list(definition.nodes),
        workspace_id=workspace["id"],
    )

    execute_node_once(
        job_db=queries,
        definition=definition,
        job=job,
        node_key="fetch_question_context",
        logs_dir=tmp_path / "logs",
        jobs_dir=tmp_path / "jobs",
    )

    runs = queries.list_node_runs(job["id"])
    assert len(runs) == 1
    assert runs[0]["log_path"].startswith("logs/")
    assert not Path(runs[0]["log_path"]).is_absolute()


def test_execute_pi_node_run_persists_relative_paths(tmp_path, monkeypatch):
    fake_pi = tmp_path / "fake_pi"
    fake_pi.write_text(
        "#!/bin/bash\n"
        'echo \'{"event":"done"}\'\n'
        "echo '{\"questions\": []}' > key_info_raw.json\n"
        "echo '{\"summary\": {}}' > key_info_report.json\n"
    )
    fake_pi.chmod(0o755)

    skill_dir = tmp_path / "skills/question_comprehension_info/generate_key_info"
    _make_fake_skill(skill_dir)

    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    workspace = queries.create_workspace("test_ws")
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))
    job = queries.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=list(definition.nodes),
        workspace_id=workspace["id"],
    )
    job_dir = tmp_path / job["storage_dir"]
    (job_dir / "questions_parsed.json").write_text(
        json.dumps({"questions": [{"question_id": "Q100"}]}), encoding="utf-8"
    )

    pi_runner = PiRunner.from_config(
        {"binary": str(fake_pi), "timeout_seconds": 10},
        skill_root=tmp_path / "skills",
    )

    execute_node_once(
        job_db=queries,
        definition=definition,
        job=job,
        node_key="generate_key_info",
        logs_dir=tmp_path / "logs",
        pi_runner=pi_runner,
        skill_root=tmp_path / "skills",
        jobs_dir=tmp_path / "jobs",
    )

    runs = queries.list_node_runs(job["id"])
    assert len(runs) == 1
    assert runs[0]["log_path"].startswith("jobs/")
    assert runs[0]["run_dir"].startswith("jobs/")
    assert runs[0]["session_dir"].startswith("jobs/")
