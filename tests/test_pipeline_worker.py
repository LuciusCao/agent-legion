import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app.cms.question import CmsQuestionDetail
from server.app.jobs import JobQueries
from server.app.pipeline_worker_thread import process_ready_pipeline_node
from server.app.pipelines.definition import load_pipeline_definition
from server.app.pipelines.executor import (
    execute_agent_node_once,
    execute_node_once,
)
from server.app.pipelines.pi_runner import PiRunner


def test_execute_fetch_question_context_writes_artifact(tmp_path):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    definition = load_pipeline_definition(Path("config/pipelines/question_content.yaml"))
    job = queries.create_job(
        pipeline_key="question_content",
        source_type="question_id",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=list(definition.nodes),
    )

    completed = execute_node_once(
        job_db=queries,
        definition=definition,
        job=job,
        node_key="fetch_question_context",
        logs_dir=tmp_path / "logs",
    )

    assert completed is True
    artifact = Path(job["storage_dir"]) / "question_context.json"
    assert json.loads(artifact.read_text(encoding="utf-8")) == {
        "question_id": "Q100",
        "title": "Question Q100",
        "source_type": "question_id",
        "normalized": {},
        "cms_payload": None,
    }
    assert queries.get_job_node(job["id"], "fetch_question_context")["status"] == "completed"


def test_execute_fetch_question_context_uses_cms_question_detail(tmp_path, monkeypatch):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    definition = load_pipeline_definition(Path("config/pipelines/question_content.yaml"))
    workspace = queries.create_workspace(
        "Math V5",
        cms_config={"question_detail_url": "https://cms.example/question/detail?subject_id=5"},
    )
    batch = queries.create_batch(
        "question_content",
        "question_ids",
        {
            "question_ids": ["Q100"],
            "cms_config": {
                "question_detail_url": "https://cms.example/question/detail?subject_id=9"
            },
        },
        workspace_id=workspace["id"],
    )
    job = queries.create_job(
        pipeline_key="question_content",
        source_type="question_id",
        source_id="Q100",
        batch_id=batch["id"],
        title="Question Q100",
        node_keys=list(definition.nodes),
        workspace_id=workspace["id"],
    )
    calls = []

    def fake_fetch_question_detail(question_id, api_url=None, token=None):
        calls.append({"question_id": question_id, "api_url": api_url, "token": token})
        return CmsQuestionDetail(
            question_id="Q100",
            title="CMS 题目一",
            normalized={"stem": "1 + 1 = ?"},
            payload={"data": {"uuid": "Q100", "title": "CMS 题目一"}},
        )

    monkeypatch.setattr(
        "server.app.pipelines.question_content.fetch_question_detail",
        fake_fetch_question_detail,
    )
    monkeypatch.setattr(
        "server.app.pipelines.question_content.get_token", lambda env, config: "token"
    )

    completed = execute_node_once(
        job_db=queries,
        definition=definition,
        job=job,
        node_key="fetch_question_context",
        logs_dir=tmp_path / "logs",
        settings_config={"cms": {"env": "prod"}},
    )

    assert completed is True
    assert calls == [
        {
            "question_id": "Q100",
            "api_url": "https://cms.example/question/detail?subject_id=9",
            "token": "token",
        }
    ]
    artifact = Path(job["storage_dir"]) / "question_context.json"
    assert json.loads(artifact.read_text(encoding="utf-8")) == {
        "question_id": "Q100",
        "title": "CMS 题目一",
        "source_type": "question_id",
        "normalized": {"stem": "1 + 1 = ?"},
        "cms_payload": {"data": {"uuid": "Q100", "title": "CMS 题目一"}},
    }


def test_fetch_question_context_uses_question_detail_resource_binding(tmp_path, monkeypatch):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    definition = load_pipeline_definition(Path("config/pipelines/question_content.yaml"))
    workspace = queries.create_workspace(
        "Resource Math",
        resource_config={
            "resources": {
                "question_detail": {
                    "provider": "cms.question.detail",
                    "config": {
                        "bank_version": "v5",
                        "subject_id": "5",
                    },
                }
            }
        },
    )
    batch = queries.create_batch(
        "question_content",
        "question_ids",
        {"question_ids": ["Q200"]},
        workspace_id=workspace["id"],
    )
    job = queries.create_job(
        pipeline_key="question_content",
        source_type="question_id",
        source_id="Q200",
        batch_id=batch["id"],
        title="Question Q200",
        node_keys=list(definition.nodes),
        workspace_id=workspace["id"],
    )
    calls = []

    def fake_fetch_question_detail(question_id, api_url=None, token=None):
        calls.append({"question_id": question_id, "api_url": api_url, "token": token})
        return CmsQuestionDetail(
            question_id="Q200",
            title="资源绑定题目",
            normalized={},
            payload={"data": {"uuid": "Q200"}},
        )

    monkeypatch.setattr(
        "server.app.pipelines.question_content.fetch_question_detail",
        fake_fetch_question_detail,
    )
    monkeypatch.setattr(
        "server.app.pipelines.question_content.get_token", lambda env, config: "token"
    )

    completed = execute_node_once(
        job_db=queries,
        definition=definition,
        job=job,
        node_key="fetch_question_context",
        logs_dir=tmp_path / "logs",
        settings_config={
            "cms": {"env": "prod"},
            "resource_providers": {
                "cms.question.detail": {
                    "api_url": "https://cms.example/question/detail",
                }
            },
        },
    )

    assert completed is True
    assert calls == [
        {
            "question_id": "Q200",
            "api_url": "https://cms.example/question/detail?bank_version=v5&subject_id=5",
            "token": "token",
        }
    ]


def test_process_ready_pipeline_node_runs_root(tmp_path):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    definition = load_pipeline_definition(Path("config/pipelines/question_content.yaml"))
    job = queries.create_job(
        pipeline_key="question_content",
        source_type="question_id",
        source_id="Q101",
        batch_id="",
        title="Question Q101",
        node_keys=list(definition.nodes),
    )

    processed = process_ready_pipeline_node(
        job_db=queries,
        definition=definition,
        logs_dir=tmp_path / "logs",
    )

    assert processed is True
    assert (Path(job["storage_dir"]) / "question_context.json").exists()


def test_process_ready_pipeline_node_marks_missing_local_handler_failed(tmp_path):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    definition = load_pipeline_definition(Path("config/pipelines/question_content.yaml"))
    job = queries.create_job(
        pipeline_key="question_content",
        source_type="question_id",
        source_id="Q102",
        batch_id="",
        title="Question Q102",
        node_keys=list(definition.nodes),
    )
    for node_key in definition.nodes:
        queries.update_job_node(job["id"], node_key, status="completed")
    queries.update_job_node(job["id"], "assemble_package", status="pending")
    for artifact_name in definition.nodes["assemble_package"].inputs:
        (Path(job["storage_dir"]) / artifact_name).write_text("{}", encoding="utf-8")

    processed = process_ready_pipeline_node(
        job_db=queries,
        definition=definition,
        logs_dir=tmp_path / "logs",
    )

    assert processed is True
    node = queries.get_job_node(job["id"], "assemble_package")
    assert node["status"] == "failed"
    assert "No local handler" in node["error_message"]
    refreshed = queries.get_job(job["id"])
    assert refreshed["status"] == "failed"
    assert "No local handler" in refreshed["error_message"]


def _make_fake_skill(skill_dir: Path) -> None:
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# skill", encoding="utf-8")
    (skill_dir / "references" / "output-contract.md").write_text("# contract", encoding="utf-8")
    validator = skill_dir / "scripts" / "validate_output.py"
    validator.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from pathlib import Path\n"
        "job_dir = Path(sys.argv[1])\n"
        "(job_dir / 'keywords_raw.json').write_text('{\"questions\": []}')\n"
        "(job_dir / 'keywords_report.json').write_text('{\"summary\": {}}')\n"
    )
    validator.chmod(0o755)


def test_execute_agent_node_once_runs_pi_node(tmp_path, monkeypatch):
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
    definition = load_pipeline_definition(Path("config/pipelines/reading_analysis.yaml"))
    job = queries.create_job(
        pipeline_key="reading_analysis",
        source_type="question",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=list(definition.nodes),
    )
    job_dir = Path(job["storage_dir"])
    (job_dir / "questions_parsed.json").write_text(
        json.dumps({"questions": [{"question_id": "Q100"}]}), encoding="utf-8"
    )

    pi_runner = PiRunner.from_config(
        {"binary": str(fake_pi), "timeout_seconds": 10},
        skill_root=tmp_path / "skills",
    )

    completed = execute_agent_node_once(
        job_db=queries,
        definition=definition,
        job=job,
        node_key="extract_keywords",
        pi_runner=pi_runner,
        skill_root=tmp_path / "skills",
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
    definition = load_pipeline_definition(Path("config/pipelines/reading_analysis.yaml"))
    job = queries.create_job(
        pipeline_key="reading_analysis",
        source_type="question",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=list(definition.nodes),
    )
    job_dir = Path(job["storage_dir"])
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
    )

    assert completed is True
    node = queries.get_job_node(job["id"], "extract_keywords")
    assert node["status"] == "completed"


def test_execute_node_once_raises_when_pi_runner_missing_for_agent_node(tmp_path):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    definition = load_pipeline_definition(Path("config/pipelines/reading_analysis.yaml"))
    job = queries.create_job(
        pipeline_key="reading_analysis",
        source_type="question",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=list(definition.nodes),
    )
    job_dir = Path(job["storage_dir"])
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
        )


def test_pipeline_worker_does_not_start_when_app_worker_disabled(tmp_path, monkeypatch):
    from server.app import main as app_main

    started: list[bool] = []

    class FakePipelineWorkerThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            started.append(True)

        def stop(self, timeout: float = 3):
            pass

    monkeypatch.setattr(app_main, "PipelineWorkerThread", FakePipelineWorkerThread)
    app = app_main.create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True

    with TestClient(app):
        pass

    assert started == []


def test_pipeline_worker_schedules_reading_analysis_local_nodes(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from server.app.pipeline_worker_thread import PipelineWorkerThread
    from server.app.pipelines.registry import load_registered_pipeline
    from server.app.settings import Settings

    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    definition = load_registered_pipeline(Path("."), "reading_analysis")
    job = queries.create_job(
        pipeline_key="reading_analysis",
        source_type="question",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=list(definition.nodes),
    )

    settings = Settings(
        root_dir=Path("."),
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={"pipelines": {"enabled": True, "pi": {"binary": "echo", "timeout_seconds": 1}}},
    )

    worker = PipelineWorkerThread(queries, settings)
    worker._definition = definition
    worker._local_executor = ThreadPoolExecutor(max_workers=definition.concurrency.local)
    worker._agent_executor = ThreadPoolExecutor(max_workers=definition.concurrency.agent)
    worker._skill_root = tmp_path / "skills"
    worker._skill_root.mkdir(parents=True)

    processed = worker._poll()

    assert processed is True
    assert len(worker._futures) == 1
    key = (job["id"], "fetch_questions")
    assert key in worker._futures

    # Wait for completion and poll again to reap
    future = worker._futures[key]
    future.result(timeout=5)
    processed = worker._poll()
    assert key not in worker._futures
    node = queries.get_job_node(job["id"], "fetch_questions")
    assert node["status"] == "completed"

    worker.stop()


def test_pipeline_worker_skips_duplicate_submissions(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from server.app.pipeline_worker_thread import PipelineWorkerThread
    from server.app.pipelines.registry import load_registered_pipeline
    from server.app.settings import Settings

    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    definition = load_registered_pipeline(Path("."), "reading_analysis")
    queries.create_job(
        pipeline_key="reading_analysis",
        source_type="question",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=list(definition.nodes),
    )

    settings = Settings(
        root_dir=Path("."),
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={"pipelines": {"enabled": True, "pi": {"binary": "echo", "timeout_seconds": 1}}},
    )

    worker = PipelineWorkerThread(queries, settings)
    worker._definition = definition
    worker._local_executor = ThreadPoolExecutor(max_workers=definition.concurrency.local)
    worker._agent_executor = ThreadPoolExecutor(max_workers=definition.concurrency.agent)
    worker._skill_root = tmp_path / "skills"
    worker._skill_root.mkdir(parents=True)

    # First poll submits fetch_questions
    processed = worker._poll()
    assert processed is True
    assert len(worker._futures) == 1

    # Second poll should not resubmit the same node
    processed = worker._poll()
    assert len(worker._futures) == 1

    worker.stop()


def test_pipeline_worker_does_not_schedule_question_content(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from server.app.pipeline_worker_thread import PipelineWorkerThread
    from server.app.pipelines.registry import load_registered_pipeline
    from server.app.settings import Settings

    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    definition = load_registered_pipeline(Path("."), "reading_analysis")
    queries.create_job(
        pipeline_key="question_content",
        source_type="question_id",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=["fetch_question_context"],
    )

    settings = Settings(
        root_dir=Path("."),
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={"pipelines": {"enabled": True, "pi": {"binary": "echo", "timeout_seconds": 1}}},
    )

    worker = PipelineWorkerThread(queries, settings)
    worker._definition = definition
    worker._local_executor = ThreadPoolExecutor(max_workers=definition.concurrency.local)
    worker._agent_executor = ThreadPoolExecutor(max_workers=definition.concurrency.agent)
    worker._skill_root = tmp_path / "skills"
    worker._skill_root.mkdir(parents=True)

    processed = worker._poll()

    # question_content job should not be scheduled by reading_analysis worker
    assert processed is False
    assert len(worker._futures) == 0

    worker.stop()


def test_pipeline_worker_graceful_shutdown(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from server.app.pipeline_worker_thread import PipelineWorkerThread
    from server.app.pipelines.registry import load_registered_pipeline
    from server.app.settings import Settings

    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    definition = load_registered_pipeline(Path("."), "reading_analysis")
    queries.create_job(
        pipeline_key="reading_analysis",
        source_type="question",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=list(definition.nodes),
    )

    settings = Settings(
        root_dir=Path("."),
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={"pipelines": {"enabled": True, "pi": {"binary": "echo", "timeout_seconds": 1}}},
    )

    worker = PipelineWorkerThread(queries, settings)
    worker._definition = definition
    worker._local_executor = ThreadPoolExecutor(max_workers=definition.concurrency.local)
    worker._agent_executor = ThreadPoolExecutor(max_workers=definition.concurrency.agent)
    worker._skill_root = tmp_path / "skills"
    worker._skill_root.mkdir(parents=True)

    worker._poll()
    assert len(worker._futures) == 1

    # Shutdown should wait for the submitted task
    worker.stop()
    assert worker._local_executor._shutdown is True
    assert worker._agent_executor._shutdown is True
