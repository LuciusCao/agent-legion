import pytest

from server.app.services.job_errors import InvalidOperationError
from server.app.services.job_intake import JobIntakeService


def test_create_batch_requires_existing_active_revision(job_db, settings, agent_manager):
    workspace = job_db.create_workspace("ws-no-revision", default_workflow_key="demo_workflow")
    service = JobIntakeService(
        job_db,
        settings,
        job_event_manager=None,
    )
    with pytest.raises(InvalidOperationError, match="no active workflow revision"):
        service.create_batch(
            workspace["id"],
            {
                "workflow_key": "demo_workflow",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q1"],
            },
        )
