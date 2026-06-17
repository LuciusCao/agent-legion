from pathlib import Path

import pytest

from server.app.jobs.queries import JobQueries
from server.app.pipelines.definition import load_pipeline_definition


def test_create_batch_and_question_jobs(tmp_path):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")

    batch = queries.create_batch(
        workflow_key="question_content",
        source_kind="mixed",
        source_payload={"knowledge_codes": ["K001"], "question_ids": ["Q001"]},
    )
    job = queries.create_job(
        workflow_key="question_content",
        source_type="question_id",
        source_id="Q001",
        batch_id=batch["id"],
        title="Question Q001",
        node_keys=["fetch_question_context", "assemble_package"],
    )

    assert batch["workflow_key"] == "question_content"
    assert batch["workspace_id"] == "default"
    assert job["id"] == "default_question_content_Q001"
    assert job["workspace_id"] == "default"
    assert job["storage_dir"].endswith("jobs/default/default_question_content_Q001")
    nodes = queries.list_job_nodes(job["id"])
    assert [node["node_key"] for node in nodes] == [
        "fetch_question_context",
        "assemble_package",
    ]
    assert {node["status"] for node in nodes} == {"pending"}


def test_workspaces_isolate_jobs_with_same_source_id(tmp_path):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    workspace = queries.create_workspace("Math Sprint")

    default_job = queries.create_job(
        workflow_key="question_content",
        source_type="question_id",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=["fetch_question_context"],
    )
    workspace_job = queries.create_job(
        workflow_key="question_content",
        source_type="question_id",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=["fetch_question_context"],
        workspace_id=workspace["id"],
    )

    assert workspace["id"] == "math_sprint"
    assert default_job["id"] == "default_question_content_Q100"
    assert workspace_job["id"] == "math_sprint_question_content_Q100"
    assert [job["id"] for job in queries.list_jobs(workspace_id="default")] == [default_job["id"]]
    assert [job["id"] for job in queries.list_jobs(workspace_id=workspace["id"])] == [
        workspace_job["id"]
    ]
    assert {job["id"] for job in queries.list_jobs(workspace_id=None)} == {
        default_job["id"],
        workspace_job["id"],
    }


def test_node_run_lifecycle(tmp_path):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    job = queries.create_job(
        workflow_key="question_content",
        source_type="question_id",
        source_id="Q002",
        batch_id="",
        title="Question Q002",
        node_keys=["fetch_question_context"],
    )

    run = queries.start_node_run(job["id"], "fetch_question_context", ["local"], "log.txt")
    queries.finish_node_run(run["id"], "completed", 0, "")
    node = queries.get_job_node(job["id"], "fetch_question_context")

    assert node is not None
    assert node["status"] == "completed"
    runs = queries.list_node_runs(job["id"])
    assert runs[0]["command_json"] == '["local"]'
    assert runs[0]["status"] == "completed"


def test_start_node_run_claims_each_node_only_once(tmp_path):
    db_path = tmp_path / "video_hive.sqlite"
    first = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    second = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    job = first.create_job(
        workflow_key="question_content",
        source_type="question_id",
        source_id="Q-CLAIM",
        batch_id="",
        title="Question Q-CLAIM",
        node_keys=["fetch_question_context"],
    )

    claimed = first.start_node_run(job["id"], "fetch_question_context", ["local"], "first.log")
    lost_claim = second.start_node_run(job["id"], "fetch_question_context", ["local"], "second.log")

    assert claimed is not None
    assert lost_claim is None
    assert len(first.list_node_runs(job["id"])) == 1


def test_create_job_rejects_identity_collision(tmp_path):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    queries.create_job(
        workflow_key="question_content",
        source_type="question_id",
        source_id="Q003",
        batch_id="",
        title="Question Q003",
        node_keys=["fetch_question_context"],
    )

    with pytest.raises(ValueError, match="identity collision"):
        queries.create_job(
            workflow_key="question_content",
            source_type="knowledge_code",
            source_id="Q003",
            batch_id="",
            title="Knowledge Q003",
            node_keys=["fetch_question_context"],
        )


def test_start_node_run_rejects_missing_node(tmp_path):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    job = queries.create_job(
        workflow_key="question_content",
        source_type="question_id",
        source_id="Q004",
        batch_id="",
        title="Question Q004",
        node_keys=["fetch_question_context"],
    )

    with pytest.raises(ValueError, match="Unknown job node"):
        queries.start_node_run(job["id"], "missing_node", ["local"], "log.txt")

    assert queries.list_node_runs(job["id"]) == []


def test_mark_node_for_rerun_marks_downstream_stale(tmp_path):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    definition = load_pipeline_definition(Path("config/pipelines/question_content.yaml"))
    job = queries.create_job(
        workflow_key="question_content",
        source_type="question_id",
        source_id="Q200",
        batch_id="",
        title="Question Q200",
        node_keys=list(definition.nodes),
    )
    for node_key in definition.nodes:
        queries.update_job_node(job["id"], node_key, status="completed", error_message="old error")

    queries.mark_node_for_rerun(
        job["id"],
        "question_understanding",
        ["misconception_analysis", "assemble_package"],
    )

    selected = queries.get_job_node(job["id"], "question_understanding")
    downstream = queries.get_job_node(job["id"], "misconception_analysis")
    terminal = queries.get_job_node(job["id"], "assemble_package")
    rerun_job = queries.get_job(job["id"])

    assert selected is not None
    assert selected["status"] == "pending"
    assert selected["stale_reason"] == ""
    assert selected["error_message"] == ""
    assert selected["started_at"] is None
    assert selected["finished_at"] is None
    assert downstream is not None
    assert downstream["status"] == "stale"
    assert downstream["error_message"] == ""
    assert terminal is not None
    assert terminal["stale_reason"] == "upstream question_understanding rerun"
    assert rerun_job is not None
    assert rerun_job["status"] == "queued"
    assert rerun_job["error_message"] == ""


def test_mark_node_for_rerun_rejects_missing_persisted_node(tmp_path):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    job = queries.create_job(
        workflow_key="question_content",
        source_type="question_id",
        source_id="Q202",
        batch_id="",
        title="Question Q202",
        node_keys=["fetch_question_context"],
    )

    with pytest.raises(ValueError, match="Unknown job node"):
        queries.mark_node_for_rerun(job["id"], "question_understanding", [])


def test_start_node_run_clears_stale_reason(tmp_path):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    job = queries.create_job(
        workflow_key="question_content",
        source_type="question_id",
        source_id="Q203",
        batch_id="",
        title="Question Q203",
        node_keys=["fetch_question_context"],
    )
    queries.update_job_node(
        job["id"],
        "fetch_question_context",
        status="stale",
        stale_reason="upstream question_understanding rerun",
    )

    run = queries.start_node_run(job["id"], "fetch_question_context", ["local"], "log.txt")
    running = queries.get_job_node(job["id"], "fetch_question_context")
    queries.finish_node_run(run["id"], "completed", 0, "")
    completed = queries.get_job_node(job["id"], "fetch_question_context")

    assert running["stale_reason"] == ""
    assert completed["stale_reason"] == ""


def test_start_node_run_persists_run_and_session_directories(tmp_path):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    job = queries.create_job(
        workflow_key="question_content",
        source_type="question_id",
        source_id="Q203",
        batch_id="",
        title="Question Q203",
        node_keys=["fetch_question_context"],
    )
    run = queries.start_node_run(
        job["id"],
        "fetch_question_context",
        ["pi", "--mode", "json"],
        str(tmp_path / "events.jsonl"),
        run_dir=str(tmp_path / "run-1"),
        session_dir=str(tmp_path / "run-1/session"),
    )

    assert run["run_dir"] == str(tmp_path / "run-1")
    assert run["session_dir"] == str(tmp_path / "run-1/session")
