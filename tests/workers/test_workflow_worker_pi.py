from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.app.jobs import JobQueries
from server.app.workflows.definition import (
    load_workflow_definition,
)
from server.app.workflows.executor import (
    execute_node_once,
)
from server.app.workflows.pi_runner import PiRunner
from tests.workers.helpers import _make_fake_skill


def test_execute_node_once_runs_pi_node(tmp_path, monkeypatch):
    fake_pi = tmp_path / "fake_pi"
    fake_pi.write_text(
        "#!/bin/bash\n"
        'echo \'{"event":"done"}\'\n'
        "echo '{\"questions\": []}' > keywords_raw.json\n"
        "echo '{\"summary\": {}}' > keywords_report.json\n"
    )
    fake_pi.chmod(0o755)

    skill_dir = tmp_path / "skills/reading_analysis/extract_keywords"
    _make_fake_skill(skill_dir)

    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    definition = load_workflow_definition(Path("config/workflows/reading_analysis.yaml"))
    job = queries.create_job(
        workflow_key="reading_analysis",
        source_type="question",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=list(definition.nodes),
    )
    job_dir = tmp_path / job["storage_dir"]
    (job_dir / "questions_parsed.json").write_text(
        json.dumps({"questions": [{"question_id": "Q100"}]}), encoding="utf-8"
    )

    pi_runner = PiRunner.from_config(
        {"binary": str(fake_pi), "timeout_seconds": 10},
        skill_root=tmp_path / "skills",
    )

    completed = execute_node_once(
        job_db=queries,
        definition=definition,
        job=job,
        node_key="extract_keywords",
        logs_dir=tmp_path / "logs",
        pi_runner=pi_runner,
        skill_root=tmp_path / "skills",
        jobs_dir=tmp_path / "jobs",
    )

    assert completed is True
    assert (job_dir / "keywords_raw.json").is_file()
    assert (job_dir / "keywords_report.json").is_file()
    node = queries.get_job_node(job["id"], "extract_keywords")
    assert node["status"] == "completed"


def test_execute_node_once_dispatches_agent_node(tmp_path, monkeypatch):
    fake_pi = tmp_path / "fake_pi"
    fake_pi.write_text(
        "#!/bin/bash\n"
        'echo \'{"event":"done"}\'\n'
        "echo '{\"questions\": []}' > keywords_raw.json\n"
        "echo '{\"summary\": {}}' > keywords_report.json\n"
    )
    fake_pi.chmod(0o755)

    skill_dir = tmp_path / "skills/reading_analysis/extract_keywords"
    _make_fake_skill(skill_dir)

    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    definition = load_workflow_definition(Path("config/workflows/reading_analysis.yaml"))
    job = queries.create_job(
        workflow_key="reading_analysis",
        source_type="question",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=list(definition.nodes),
    )
    job_dir = tmp_path / job["storage_dir"]
    (job_dir / "questions_parsed.json").write_text(
        json.dumps({"questions": [{"question_id": "Q100"}]}), encoding="utf-8"
    )

    pi_runner = PiRunner.from_config(
        {"binary": str(fake_pi), "timeout_seconds": 10},
        skill_root=tmp_path / "skills",
    )

    completed = execute_node_once(
        job_db=queries,
        definition=definition,
        job=job,
        node_key="extract_keywords",
        logs_dir=tmp_path / "logs",
        pi_runner=pi_runner,
        skill_root=tmp_path / "skills",
        jobs_dir=tmp_path / "jobs",
    )

    assert completed is True
    node = queries.get_job_node(job["id"], "extract_keywords")
    assert node["status"] == "completed"


def test_execute_node_once_raises_when_pi_runner_missing_for_agent_node(tmp_path):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    definition = load_workflow_definition(Path("config/workflows/reading_analysis.yaml"))
    job = queries.create_job(
        workflow_key="reading_analysis",
        source_type="question",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=list(definition.nodes),
    )
    job_dir = tmp_path / job["storage_dir"]
    (job_dir / "questions_parsed.json").write_text(
        json.dumps({"questions": [{"question_id": "Q100"}]}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="Pi runner is not configured"):
        execute_node_once(
            job_db=queries,
            definition=definition,
            job=job,
            node_key="extract_keywords",
            logs_dir=tmp_path / "logs",
            jobs_dir=tmp_path / "jobs",
        )
