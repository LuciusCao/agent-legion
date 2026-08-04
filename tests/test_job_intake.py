import pytest

from server.app.services.job_errors import InvalidOperationError
from server.app.services.job_intake import JobIntakeService
from server.app.services.workflow_catalog import WorkflowCatalogService


def test_create_batch_requires_existing_active_revision(job_db, settings, agent_manager):
    workspace = job_db.create_workspace(
        "ws-no-revision", default_workflow_key="question_comprehension_info"
    )
    service = JobIntakeService(
        job_db,
        settings,
        WorkflowCatalogService(settings),
        job_event_manager=None,
    )
    with pytest.raises(InvalidOperationError, match="no active workflow revision"):
        service.create_batch(
            workspace["id"],
            {
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q1"],
            },
        )
