import pytest

from server.app.jobs.queries import JobQueries


def test_create_batch_and_question_jobs(tmp_path):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")

    batch = queries.create_batch(
        pipeline_key="question_content",
        source_kind="mixed",
        source_payload={"knowledge_codes": ["K001"], "question_ids": ["Q001"]},
    )
    job = queries.create_job(
        pipeline_key="question_content",
        source_type="question_id",
        source_id="Q001",
        batch_id=batch["id"],
        title="Question Q001",
        node_keys=["fetch_question_context", "assemble_package"],
    )

    assert batch["pipeline_key"] == "question_content"
    assert job["id"] == "question_content_Q001"
    assert job["storage_dir"].endswith("jobs/question_content_Q001")
    nodes = queries.list_job_nodes(job["id"])
    assert [node["node_key"] for node in nodes] == [
        "fetch_question_context",
        "assemble_package",
    ]
    assert {node["status"] for node in nodes} == {"pending"}


def test_node_run_lifecycle(tmp_path):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    job = queries.create_job(
        pipeline_key="question_content",
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


def test_create_job_rejects_identity_collision(tmp_path):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    queries.create_job(
        pipeline_key="question_content",
        source_type="question_id",
        source_id="Q003",
        batch_id="",
        title="Question Q003",
        node_keys=["fetch_question_context"],
    )

    with pytest.raises(ValueError, match="identity collision"):
        queries.create_job(
            pipeline_key="question_content",
            source_type="knowledge_code",
            source_id="Q003",
            batch_id="",
            title="Knowledge Q003",
            node_keys=["fetch_question_context"],
        )


def test_start_node_run_rejects_missing_node(tmp_path):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    job = queries.create_job(
        pipeline_key="question_content",
        source_type="question_id",
        source_id="Q004",
        batch_id="",
        title="Question Q004",
        node_keys=["fetch_question_context"],
    )

    with pytest.raises(ValueError, match="Unknown job node"):
        queries.start_node_run(job["id"], "missing_node", ["local"], "log.txt")

    assert queries.list_node_runs(job["id"]) == []
