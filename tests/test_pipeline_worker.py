import json
from pathlib import Path

from fastapi.testclient import TestClient

from server.app.jobs import JobQueries
from server.app.pipeline_worker_thread import process_ready_pipeline_node
from server.app.pipelines.definition import load_pipeline_definition
from server.app.pipelines.executor import execute_node_once


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
    }
    assert queries.get_job_node(job["id"], "fetch_question_context")["status"] == "completed"


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
