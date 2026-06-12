import pytest

from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.job_intake import JobIntakeService
from server.app.services.pipeline_catalog import PipelineCatalogService


@pytest.fixture
def intake_service(job_db, settings):
    return JobIntakeService(job_db, settings, PipelineCatalogService(settings))


def test_job_intake_creates_direct_id_jobs(job_db, settings):
    job_db.get_workspace("default")
    service = JobIntakeService(job_db, settings, PipelineCatalogService(settings))

    result = service.create_batch(
        "default",
        {
            "pipeline_key": "question_content",
            "source_kind": "direct_ids",
            "entity": "question",
            "question_ids": ["Q1", "Q1", " Q2 "],
            "knowledge_codes": [],
        },
    )

    assert result["created_count"] == 2
    assert [job["source_id"] for job in result["jobs"]] == ["Q1", "Q2"]


def test_job_intake_rejects_missing_workspace(intake_service):
    with pytest.raises(NotFoundError, match="Workspace not found"):
        intake_service.create_batch(
            "missing",
            {
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q1"],
                "knowledge_codes": [],
            },
        )


def test_job_intake_rejects_disabled_mode(intake_service):
    workspace = intake_service.job_db.get_workspace("default")
    workspace = intake_service.job_db.update_workspace(
        workspace["id"], intake_config={"enabled_modes": ["by_knowledge"]}
    )

    with pytest.raises(InvalidOperationError, match="Intake mode is disabled"):
        intake_service.create_batch(
            workspace["id"],
            {
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q1"],
                "knowledge_codes": [],
            },
        )


def test_job_intake_rejects_unsupported_entity_mode(intake_service):
    with pytest.raises(InvalidOperationError, match="Unsupported entity and intake mode"):
        intake_service.create_batch(
            "default",
            {
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
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
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": [],
                "knowledge_codes": [],
            },
        )
