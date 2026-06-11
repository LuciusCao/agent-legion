import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from server.app.cms.question import CmsQuestionDetail
from server.app.jobs import JobQueries
from server.app.pipeline_worker_thread import (
    PipelineWorkerThread,
    _execute_node_wrapped,
    process_ready_pipeline_node,
)
from server.app.pipelines.definition import (
    PipelineAgent,
    PipelineConcurrency,
    PipelineDefinition,
    PipelineIntake,
    PipelineNode,
    load_pipeline_definition,
)
from server.app.pipelines.executor import (
    execute_agent_node_once,
    execute_node_once,
)
from server.app.pipelines.pi_runner import PiRunner
from tests.helpers import make_pipeline_worker


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
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    worker, definition = make_pipeline_worker(tmp_path, queries)
    job = queries.create_job(
        pipeline_key="reading_analysis",
        source_type="question",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=list(definition.nodes),
    )

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
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    worker, definition = make_pipeline_worker(tmp_path, queries)
    queries.create_job(
        pipeline_key="reading_analysis",
        source_type="question",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=list(definition.nodes),
    )

    # Block the wrapped execution so the future stays in-flight across polls.
    import threading as _threading

    _blocker = _threading.Event()

    def _slow_execute(*args, **kwargs):
        _blocker.wait(timeout=5)
        return True

    monkeypatch.setattr("server.app.pipeline_worker_thread._execute_node_wrapped", _slow_execute)

    # First poll submits fetch_questions
    processed = worker._poll()
    assert processed is True
    assert len(worker._futures) == 1

    # Second poll should not resubmit the same node
    processed = worker._poll()
    assert len(worker._futures) == 1

    _blocker.set()
    worker.stop()


def test_pipeline_worker_does_not_schedule_question_content(tmp_path, monkeypatch):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    worker, _definition = make_pipeline_worker(tmp_path, queries)
    queries.create_job(
        pipeline_key="question_content",
        source_type="question_id",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=["fetch_question_context"],
    )

    processed = worker._poll()

    # question_content job should not be scheduled by reading_analysis worker
    assert processed is False
    assert len(worker._futures) == 0

    worker.stop()


def test_pipeline_worker_graceful_shutdown(tmp_path, monkeypatch):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    worker, definition = make_pipeline_worker(tmp_path, queries)
    queries.create_job(
        pipeline_key="reading_analysis",
        source_type="question",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=list(definition.nodes),
    )

    worker._poll()
    assert len(worker._futures) == 1

    # Shutdown should wait for the submitted task
    worker.stop()
    assert worker._local_executor._shutdown is True
    assert worker._agent_executor._shutdown is True


def test_pipeline_worker_start_handles_missing_pi_config(tmp_path, monkeypatch):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    worker, definition = make_pipeline_worker(
        tmp_path, queries, pi_binary=None, with_executors=False
    )
    queries.create_job(
        pipeline_key="reading_analysis",
        source_type="question",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=list(definition.nodes),
    )

    # start() should not raise even though pi config is missing
    worker.start()
    assert worker._pi_runner is None
    assert worker._thread is not None
    worker.stop()


def test_pipeline_worker_fails_agent_node_when_pi_runner_missing(tmp_path, monkeypatch):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    worker, definition = make_pipeline_worker(tmp_path, queries, pi_binary=None)
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

    # Run fetch_questions (local) and wait for it to finish.
    processed = worker._poll()
    assert processed is True
    worker._futures[(job["id"], "fetch_questions")].result(timeout=5)

    # Run clean_and_parse (local) and wait for it to finish.
    processed = worker._poll()
    assert processed is True
    worker._futures[(job["id"], "clean_and_parse")].result(timeout=5)

    # extract_keywords (agent) fails synchronously because pi runner is missing.
    processed = worker._poll()
    assert processed is True

    node = queries.get_job_node(job["id"], "extract_keywords")
    assert node["status"] == "failed"
    assert "Pi runner is not configured" in node["error_message"]
    assert queries.get_job(job["id"])["status"] == "failed"

    worker.stop()


def _make_test_definition(nodes: list[PipelineNode]) -> PipelineDefinition:
    return PipelineDefinition(
        key="test",
        label="Test",
        concurrency=PipelineConcurrency(local=1, agent=1),
        intake=PipelineIntake(),
        nodes={n.key: n for n in nodes},
    )


def test_execute_node_wrapped_falls_back_to_direct_update_when_no_run_exists():
    job_db = MagicMock()
    job_db.list_node_runs.return_value = []
    definition = MagicMock()
    job = {"id": "job_1"}

    with patch(
        "server.app.pipeline_worker_thread.execute_node_once",
        side_effect=RuntimeError("boom"),
    ):
        result = _execute_node_wrapped(
            job_db,
            definition,
            job,
            "node_a",
            Path("/tmp/logs"),
            None,
            None,
            None,
        )

    assert result is False
    job_db.finish_node_run.assert_not_called()
    job_db.update_job_node.assert_called_once_with(
        "job_1", "node_a", status="failed", error_message="boom"
    )
    job_db.update_job_status.assert_called_once_with("job_1", "failed", "boom")


def test_execute_node_wrapped_finishes_latest_running_run_when_one_exists():
    job_db = MagicMock()
    job_db.list_node_runs.return_value = [
        {"id": 1, "node_key": "node_a", "status": "completed"},
        {"id": 2, "node_key": "node_a", "status": "running"},
        {"id": 3, "node_key": "node_b", "status": "running"},
    ]
    definition = MagicMock()
    job = {"id": "job_1"}

    with patch(
        "server.app.pipeline_worker_thread.execute_node_once",
        side_effect=RuntimeError("boom"),
    ):
        result = _execute_node_wrapped(
            job_db,
            definition,
            job,
            "node_a",
            Path("/tmp/logs"),
            None,
            None,
            None,
        )

    assert result is False
    job_db.finish_node_run.assert_called_once_with(2, "failed", 1, "boom")
    job_db.update_job_node.assert_not_called()
    job_db.update_job_status.assert_called_once_with("job_1", "failed", "boom")


def test_process_ready_pipeline_node_refreshes_job_status_when_no_local_nodes_ready():
    definition = _make_test_definition(
        [
            PipelineNode(
                key="agent_node",
                label="Agent",
                runner="agent",
                agent=PipelineAgent(engine="pi", skill="test/skill"),
            ),
        ]
    )
    job_db = MagicMock()
    job = {"id": "job_1", "storage_dir": "/tmp/job_1"}
    job_db.list_jobs.return_value = [job]

    ready_node = MagicMock()
    ready_node.runner = "agent"

    with (
        patch(
            "server.app.pipeline_worker_thread.find_ready_nodes",
            return_value=[ready_node],
        ) as mock_find,
        patch("server.app.pipeline_worker_thread._refresh_job_status") as mock_refresh,
        patch(
            "server.app.pipeline_worker_thread._node_statuses",
            return_value={"agent_node": "pending"},
        ),
    ):
        result = process_ready_pipeline_node(job_db, definition, Path("/tmp/logs"))

    assert result is False
    mock_find.assert_called_once()
    mock_refresh.assert_called_once_with(job_db, "job_1")


def test_pipeline_worker_cancels_pending_futures_for_paused_workspace(tmp_path):
    from concurrent.futures import Future

    from server.app.settings import Settings

    settings = Settings(
        root_dir=Path("."),
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={},
    )
    job_db = MagicMock()
    job_db.list_jobs.return_value = []
    worker = PipelineWorkerThread(job_db, settings)
    worker._definitions = [MagicMock()]

    control = MagicMock()
    control.is_paused.return_value = True
    worker.workspace_worker_control = control

    future = Future()
    worker._futures[("job_1", "node_a")] = future
    worker._local_futures.add(("job_1", "node_a"))
    worker._job_workspace_ids["job_1"] = "ws_1"

    worker._poll()

    assert future.cancelled()
    assert ("job_1", "node_a") not in worker._futures
    assert ("job_1", "node_a") not in worker._local_futures
    assert "job_1" not in worker._job_workspace_ids
    control.is_paused.assert_called_with("ws_1")


def test_pipeline_worker_reconciles_orphaned_agent_futures(tmp_path):
    from server.app.settings import Settings

    settings = Settings(
        root_dir=Path("."),
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={},
    )
    agent_manager = MagicMock()
    job_db = MagicMock()
    job_db.list_jobs.return_value = []
    worker = PipelineWorkerThread(job_db, settings, agent_manager=agent_manager)
    worker._definitions = [MagicMock()]
    worker._agent_futures = {("job_1", "node_a")}

    worker._poll()

    assert ("job_1", "node_a") not in worker._agent_futures
    agent_manager.set_idle.assert_called_once_with("pi")


def test_pipeline_worker_fails_pi_node_when_pi_runner_not_configured(tmp_path):
    from server.app.settings import Settings

    definition = _make_test_definition(
        [
            PipelineNode(
                key="pi_node",
                label="Pi Node",
                runner="agent",
                agent=PipelineAgent(engine="pi", skill="test/skill"),
            ),
        ]
    )

    settings = Settings(
        root_dir=Path("."),
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={},
    )
    job_db = MagicMock()
    job = {"id": "job_1", "storage_dir": str(tmp_path / "job_1")}
    job_db.list_jobs.return_value = [job]
    job_db.start_node_run.return_value = {"id": 42}

    worker = PipelineWorkerThread(job_db, settings)
    worker._definitions = [definition]
    worker._pi_runner = None
    worker._skill_root = None

    with patch(
        "server.app.pipeline_worker_thread._node_statuses",
        return_value={"pi_node": "pending"},
    ):
        worker._poll()

    job_db.start_node_run.assert_called_once()
    job_db.finish_node_run.assert_called_once_with(42, "failed", 1, "Pi runner is not configured")


def test_pipeline_worker_fails_generic_agent_node_when_no_runner_available(tmp_path):
    from server.app.settings import Settings

    definition = _make_test_definition(
        [
            PipelineNode(
                key="generic_agent",
                label="Generic Agent",
                runner="agent",
            ),
        ]
    )

    settings = Settings(
        root_dir=Path("."),
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={},
    )
    job_db = MagicMock()
    job = {"id": "job_1", "storage_dir": str(tmp_path / "job_1")}
    job_db.list_jobs.return_value = [job]
    job_db.start_node_run.return_value = {"id": 99}

    worker = PipelineWorkerThread(job_db, settings)
    worker._definitions = [definition]
    worker._pi_runner = None
    worker._skill_root = None

    with patch(
        "server.app.pipeline_worker_thread._node_statuses",
        return_value={"generic_agent": "pending"},
    ):
        worker._poll()

    job_db.start_node_run.assert_called_once()
    job_db.finish_node_run.assert_called_once_with(99, "failed", 1, "Pi runner is not configured")
