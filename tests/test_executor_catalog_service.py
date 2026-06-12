import pytest

from server.app.services.executor_catalog import ExecutorCatalogService
from server.app.services.job_errors import InvalidOperationError, NotFoundError


def test_executor_catalog_round_trips_workspace_assignment(job_db, settings, agent_manager):
    workspace = job_db.create_workspace("Math")
    service = ExecutorCatalogService(job_db, settings, agent_manager)
    saved = service.assign(workspace["id"], "pi", 2)
    assert saved == {
        "workspace_id": workspace["id"],
        "agent_id": "pi",
        "concurrency_limit": 2,
    }
    assert service.list_assignments(workspace["id"]) == [saved]


def test_executor_catalog_assignment_rejects_missing_workspace(job_db, settings, agent_manager):
    service = ExecutorCatalogService(job_db, settings, agent_manager)
    with pytest.raises(NotFoundError, match="Workspace not found"):
        service.assign("missing", "pi", 1)


def test_executor_catalog_assignment_rejects_invalid_limit(job_db, settings, agent_manager):
    workspace = job_db.create_workspace("Math")
    service = ExecutorCatalogService(job_db, settings, agent_manager)
    with pytest.raises(InvalidOperationError, match="concurrency_limit must be at least 1"):
        service.assign(workspace["id"], "pi", 0)
