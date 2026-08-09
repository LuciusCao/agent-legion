import pytest

from server.app.jobs.storage_layout import job_storage_dir
from server.app.services import job_intake_chunks
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.job_intake import JobIntakeService
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workflow_revisions import WorkflowRevisionService


def _create_workspace_with_revision(job_db, settings, workflow_key="question_comprehension_info"):
    workspace = job_db.create_workspace("default", default_workflow_key=workflow_key)
    definition = WorkflowCatalogService(settings).definition(workflow_key)
    WorkflowRevisionService(job_db).ensure_active_revision(workspace["id"], definition)
    return workspace


@pytest.fixture
def intake_service(job_db, settings):
    _create_workspace_with_revision(job_db, settings)
    return JobIntakeService(job_db, settings, WorkflowCatalogService(settings))


def test_job_intake_creates_direct_id_jobs(job_db, settings):
    _create_workspace_with_revision(job_db, settings)
    service = JobIntakeService(job_db, settings, WorkflowCatalogService(settings))

    result = service.create_batch(
        "default",
        {
            "workflow_key": "question_comprehension_info",
            "source_kind": "batch_by_ids",
            "entity": "question",
            "question_ids": ["Q1", "Q1", " Q2 "],
            "knowledge_codes": [],
        },
    )

    assert result["created_count"] == 2
    assert [job["source_id"] for job in result["jobs"]] == ["Q1", "Q2"]
    assert [job["storage_dir"] for job in result["jobs"]] == [
        str(
            job_storage_dir(settings.jobs_dir, "default", "default_question_comprehension_info_Q1")
        ),
        str(
            job_storage_dir(settings.jobs_dir, "default", "default_question_comprehension_info_Q2")
        ),
    ]
    for job in result["jobs"]:
        assert job_storage_dir(settings.jobs_dir, "default", job["id"]).is_dir()


def test_job_intake_rejects_missing_workspace(intake_service):
    with pytest.raises(NotFoundError, match="Workspace not found"):
        intake_service.create_batch(
            "missing",
            {
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q1"],
                "knowledge_codes": [],
            },
        )


def test_job_intake_rejects_disabled_mode(intake_service):
    workspace = intake_service.job_db.get_workspace("default")
    workspace = intake_service.job_db.update_workspace(
        workspace["id"], intake_config={"enabled_modes": ["batch_by_knowledge"]}
    )

    with pytest.raises(InvalidOperationError, match="Intake mode is disabled"):
        intake_service.create_batch(
            workspace["id"],
            {
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q1"],
                "knowledge_codes": [],
            },
        )


def test_job_intake_rejects_unsupported_entity_mode(intake_service):
    with pytest.raises(InvalidOperationError, match="Unsupported entity and intake mode"):
        intake_service.create_batch(
            "default",
            {
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "entity": "unknown_entity",
                "question_ids": ["Q1"],
                "knowledge_codes": [],
            },
        )


def test_job_intake_requires_at_least_one_value(intake_service):
    with pytest.raises(InvalidOperationError, match="At least one question"):
        intake_service.create_batch(
            "default",
            {
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": [],
                "knowledge_codes": [],
            },
        )


def test_job_intake_dedups_across_batches_without_full_row_load(job_db, settings, monkeypatch):
    _create_workspace_with_revision(job_db, settings)
    service = JobIntakeService(job_db, settings, WorkflowCatalogService(settings))

    def fail_list_jobs(*args, **kwargs):
        raise AssertionError("intake dedup must not materialize full job rows")

    monkeypatch.setattr(job_db, "list_jobs", fail_list_jobs)

    first = service.create_batch(
        "default",
        {
            "workflow_key": "question_comprehension_info",
            "source_kind": "batch_by_ids",
            "entity": "question",
            "question_ids": ["Q1", "Q2"],
            "knowledge_codes": [],
        },
    )
    second = service.create_batch(
        "default",
        {
            "workflow_key": "question_comprehension_info",
            "source_kind": "batch_by_ids",
            "entity": "question",
            "question_ids": ["Q2", "Q3"],
            "knowledge_codes": [],
        },
    )

    assert first["created_count"] == 2
    assert second["created_count"] == 1
    assert [job["source_id"] for job in second["jobs"]] == ["Q3"]


def test_list_job_dedup_keys_returns_only_key_columns(job_db, settings):
    _create_workspace_with_revision(job_db, settings)
    service = JobIntakeService(job_db, settings, WorkflowCatalogService(settings))
    service.create_batch(
        "default",
        {
            "workflow_key": "question_comprehension_info",
            "source_kind": "batch_by_ids",
            "entity": "question",
            "question_ids": ["Q1", "Q2"],
            "knowledge_codes": [],
        },
    )

    keys = job_db.list_job_dedup_keys("default")

    assert keys == {("question", "Q1"), ("question", "Q2")}


def test_job_intake_dedups_candidates_across_chunk_boundaries(job_db, settings, monkeypatch):
    from dataclasses import replace

    from server.app.services.job_intake_registry import RESOLVERS
    from server.app.services.job_intake_resolution import candidate

    _create_workspace_with_revision(job_db, settings)
    service = JobIntakeService(job_db, settings, WorkflowCatalogService(settings))
    monkeypatch.setattr(job_intake_chunks, "INTAKE_RESOLUTION_CHUNK_SIZE", 2)

    def fake_expand(entity, input_values, source_kind):
        # Node-phase intake keeps question candidates opaque, so cross-chunk
        # dedup is exercised through a resolver that expands codes into
        # overlapping question ids (one shared id per chunk).
        return [
            candidate(entity, f"Q-{code}", f"Question {code}", source_kind, code)
            for code in input_values
        ] + [candidate(entity, "Q-shared", "Shared", source_kind, input_values[0])]

    spec = RESOLVERS[("question", "batch_by_knowledge")]
    monkeypatch.setitem(
        RESOLVERS, ("question", "batch_by_knowledge"), replace(spec, handler=fake_expand)
    )

    result = service.create_batch(
        "default",
        {
            "workflow_key": "question_comprehension_info",
            "source_kind": "batch_by_knowledge",
            "entity": "question",
            "question_ids": [],
            "knowledge_codes": ["K1", "K2", "K3", "K4", "K5"],
        },
    )

    source_ids = [job["source_id"] for job in result["jobs"]]
    assert result["created_count"] == 6
    assert sorted(source_ids) == ["Q-K1", "Q-K2", "Q-K3", "Q-K4", "Q-K5", "Q-shared"]


def test_job_intake_handles_large_batch_across_default_chunks(job_db, settings):
    _create_workspace_with_revision(job_db, settings)
    service = JobIntakeService(job_db, settings, WorkflowCatalogService(settings))
    question_ids = [f"Q{i:04d}" for i in range(1200)]

    result = service.create_batch(
        "default",
        {
            "workflow_key": "question_comprehension_info",
            "source_kind": "batch_by_ids",
            "entity": "question",
            "question_ids": question_ids,
            "knowledge_codes": [],
        },
    )

    assert result["created_count"] == 1200
    assert len({job["id"] for job in result["jobs"]}) == 1200


def test_job_intake_freezes_node_code_versions(job_db, settings):
    """Intake snapshots published custom code versions into the batch payload."""
    from server.app.services.node_codes import NodeCodeService

    workspace = _create_workspace_with_revision(job_db, settings)
    codes = NodeCodeService(job_db.path)
    codes.save_draft(
        workspace["id"],
        "question_comprehension_info",
        "fetch_questions",
        "def run(job, job_dir, runtime):\n    return None\n",
        "user:u1",
    )
    codes.publish(workspace["id"], "question_comprehension_info", "fetch_questions")
    service = JobIntakeService(job_db, settings, WorkflowCatalogService(settings))

    result = service.create_batch(
        "default",
        {
            "workflow_key": "question_comprehension_info",
            "source_kind": "batch_by_ids",
            "entity": "question",
            "question_ids": ["Q1"],
            "knowledge_codes": [],
        },
    )

    batch = job_db.get_batch(str(result["jobs"][0]["batch_id"]))
    import json

    payload = json.loads(batch["source_payload_json"])
    pins = payload["node_code_versions"]
    assert pins["fetch_questions"]["version"] == 1
    assert len(pins["fetch_questions"]["code_hash"]) == 64
    # Nodes without published custom code are not pinned.
    assert "clean_and_parse" not in pins


def test_job_intake_freezes_empty_when_no_custom_codes(job_db, settings):
    _create_workspace_with_revision(job_db, settings)
    service = JobIntakeService(job_db, settings, WorkflowCatalogService(settings))

    result = service.create_batch(
        "default",
        {
            "workflow_key": "question_comprehension_info",
            "source_kind": "batch_by_ids",
            "entity": "question",
            "question_ids": ["Q1"],
            "knowledge_codes": [],
        },
    )

    batch = job_db.get_batch(str(result["jobs"][0]["batch_id"]))
    import json

    payload = json.loads(batch["source_payload_json"])
    assert payload["node_code_versions"] == {}
