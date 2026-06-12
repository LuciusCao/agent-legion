import pytest

from server.app.services.executor_catalog import ExecutorCatalogService
from server.app.services.job_errors import InvalidOperationError, NotFoundError


@pytest.fixture
def service(job_db, settings, agent_manager):
    return ExecutorCatalogService(job_db, settings, agent_manager)


def test_catalog_exposes_normalized_yaml_definitions(service: ExecutorCatalogService) -> None:
    result = service.catalog()
    assert result["executors"][0] == {
        "id": "local-default",
        "kind": "local",
        "global_capacity": 16,
        "capabilities": [
            "assemble_package",
            "clean_and_parse",
            "fetch_question_context",
            "fetch_questions",
            "mark_question",
        ],
    }


def test_workspace_configuration_reports_unknown_legacy_agents(service, job_db) -> None:
    workspace = job_db.create_workspace("Legacy")
    job_db.upsert_workspace_agent_assignment(workspace["id"], "unknown-agent", 2)
    result = service.workspace_configuration(workspace["id"])
    assert result["migration_warnings"] == [
        "Legacy agent assignment unknown-agent has no Executor mapping"
    ]


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
