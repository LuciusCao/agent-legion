import json
from pathlib import Path

from server.app.jobs import JobQueries
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
