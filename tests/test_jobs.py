import pytest

from server.app.jobs.queries import JobQueries
from tests.helpers import load_builtin_definition
from tests.helpers.job_dirs import job_storage_ref
from tests.postgres_support import TEST_DATABASE_URL


def test_create_batch_and_question_jobs(tmp_path):
    queries = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    workspace = queries.create_workspace(
        "default", default_workflow_key="education_video_problems_generation"
    )

    batch = queries.create_run(
        workflow_key="education_video_problems_generation",
        source_kind="mixed",
        digest_payload={"knowledge_codes": ["K001"], "question_ids": ["Q001"]},
        workspace_id=workspace["id"],
    )
    job = queries.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question_id",
        source_id="Q001",
        run_id=batch["id"],
        title="Question Q001",
        node_keys=["fetch_question_context", "assemble_package"],
        workspace_id=workspace["id"],
    )

    assert batch["workflow_key"] == "education_video_problems_generation"
    assert batch["workspace_id"] == "default"
    assert job["id"] == "default_education_video_problems_generation_Q001"
    assert job["workspace_id"] == "default"
    assert job["storage_dir"].endswith(
        job_storage_ref("default", "default_education_video_problems_generation_Q001")
    )
    nodes = queries.list_job_nodes(job["id"])
    assert [node["node_key"] for node in nodes] == [
        "fetch_question_context",
        "assemble_package",
    ]
    assert {node["status"] for node in nodes} == {"pending"}


def test_workspaces_isolate_jobs_with_same_source_id(tmp_path):
    queries = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    queries.create_workspace("default", default_workflow_key="education_video_problems_generation")
    workspace = queries.create_workspace(
        "Math Sprint", default_workflow_key="education_video_problems_generation"
    )

    default_job = queries.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question_id",
        source_id="Q100",
        run_id="",
        title="Question Q100",
        node_keys=["fetch_question_context"],
        workspace_id="default",
    )
    workspace_job = queries.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question_id",
        source_id="Q100",
        run_id="",
        title="Question Q100",
        node_keys=["fetch_question_context"],
        workspace_id=workspace["id"],
    )

    assert workspace["id"] == "math_sprint"
    assert default_job["id"] == "default_education_video_problems_generation_Q100"
    assert workspace_job["id"] == "math_sprint_education_video_problems_generation_Q100"
    assert [job["id"] for job in queries.list_jobs(workspace_id="default")] == [default_job["id"]]
    assert [job["id"] for job in queries.list_jobs(workspace_id=workspace["id"])] == [
        workspace_job["id"]
    ]
    assert {job["id"] for job in queries.list_jobs(workspace_id=None)} == {
        default_job["id"],
        workspace_job["id"],
    }


def test_node_run_lifecycle(tmp_path):
    queries = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    queries.create_workspace("default", default_workflow_key="education_video_problems_generation")
    job = queries.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question_id",
        source_id="Q002",
        run_id="",
        title="Question Q002",
        node_keys=["fetch_question_context"],
        workspace_id="default",
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
    db_path = TEST_DATABASE_URL
    first = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    second = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    first.create_workspace("default", default_workflow_key="education_video_problems_generation")
    job = first.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question_id",
        source_id="Q-CLAIM",
        run_id="",
        title="Question Q-CLAIM",
        node_keys=["fetch_question_context"],
        workspace_id="default",
    )

    claimed = first.start_node_run(job["id"], "fetch_question_context", ["local"], "first.log")
    lost_claim = second.start_node_run(job["id"], "fetch_question_context", ["local"], "second.log")

    assert claimed is not None
    assert lost_claim is None
    assert len(first.list_node_runs(job["id"])) == 1


def test_create_job_rejects_identity_collision(tmp_path):
    queries = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    queries.create_workspace("default", default_workflow_key="education_video_problems_generation")
    queries.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question_id",
        source_id="Q003",
        run_id="",
        title="Question Q003",
        node_keys=["fetch_question_context"],
        workspace_id="default",
    )

    with pytest.raises(ValueError, match="identity collision"):
        queries.create_job(
            workflow_key="education_video_problems_generation",
            source_type="knowledge_code",
            source_id="Q003",
            run_id="",
            title="Knowledge Q003",
            node_keys=["fetch_question_context"],
            workspace_id="default",
        )


def test_start_node_run_rejects_missing_node(tmp_path):
    queries = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    queries.create_workspace("default", default_workflow_key="education_video_problems_generation")
    job = queries.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question_id",
        source_id="Q004",
        run_id="",
        title="Question Q004",
        node_keys=["fetch_question_context"],
        workspace_id="default",
    )

    with pytest.raises(ValueError, match="Unknown job node"):
        queries.start_node_run(job["id"], "missing_node", ["local"], "log.txt")

    assert queries.list_node_runs(job["id"]) == []


def test_mark_node_for_rerun_marks_downstream_stale(tmp_path):
    queries = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    queries.create_workspace("default", default_workflow_key="education_video_problems_generation")
    definition = load_builtin_definition("education_video_problems_generation")
    job = queries.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question_id",
        source_id="Q200",
        run_id="",
        title="Question Q200",
        node_keys=list(definition.nodes),
        workspace_id="default",
    )
    for node_key in definition.nodes:
        queries.update_job_node(job["id"], node_key, status="completed", error_message="old error")

    queries.mark_node_for_rerun(
        job["id"],
        "write_script",
        ["review_script", "publish_content"],
    )

    selected = queries.get_job_node(job["id"], "write_script")
    downstream = queries.get_job_node(job["id"], "review_script")
    terminal = queries.get_job_node(job["id"], "publish_content")
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
    assert terminal["stale_reason"] == "upstream write_script rerun"
    assert rerun_job is not None
    assert rerun_job["status"] == "queued"
    assert rerun_job["error_message"] == ""


def test_mark_node_for_rerun_rejects_missing_persisted_node(tmp_path):
    queries = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    queries.create_workspace("default", default_workflow_key="education_video_problems_generation")
    job = queries.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question_id",
        source_id="Q202",
        run_id="",
        title="Question Q202",
        node_keys=["fetch_question_context"],
        workspace_id="default",
    )

    with pytest.raises(ValueError, match="Unknown job node"):
        queries.mark_node_for_rerun(job["id"], "question_understanding", [])


def test_start_node_run_clears_stale_reason(tmp_path):
    queries = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    queries.create_workspace("default", default_workflow_key="education_video_problems_generation")
    job = queries.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question_id",
        source_id="Q203",
        run_id="",
        title="Question Q203",
        node_keys=["fetch_question_context"],
        workspace_id="default",
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
    queries = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    queries.create_workspace("default", default_workflow_key="education_video_problems_generation")
    job = queries.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question_id",
        source_id="Q203",
        run_id="",
        title="Question Q203",
        node_keys=["fetch_question_context"],
        workspace_id="default",
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
