import pytest

from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.pipeline_catalog import PipelineCatalogService
from server.app.services.workspace_configuration import WorkspaceConfigurationService


@pytest.fixture
def workspace_service(job_db, settings, agent_manager):
    return WorkspaceConfigurationService(
        job_db, settings, agent_manager, PipelineCatalogService(settings)
    )


@pytest.fixture
def workspace(workspace_service):
    return workspace_service.create({"name": "Test", "default_pipeline_key": "question_content"})


def test_workspace_configuration_missing_workspace_raises_domain_error(workspace_service):
    with pytest.raises(NotFoundError, match="Workspace not found"):
        workspace_service.get("missing")


def test_workspace_configuration_rejects_unknown_settings_section(workspace_service, workspace):
    with pytest.raises(NotFoundError, match="Unknown settings section"):
        workspace_service.update_section(workspace["id"], "unknown", {})


def test_replace_configuration_saves_workspace_and_executors_in_one_transaction(
    workspace_service: WorkspaceConfigurationService,
    workspace,
) -> None:
    result = workspace_service.replace_configuration(
        workspace["id"],
        workspace_patch={"name": "Reading"},
        settings_patch={"pipelineKey": "reading_analysis"},
        executor_allocations=[{"executor_id": "local-default", "concurrency_limit": 4}],
        node_bindings=[
            {
                "pipeline_key": "reading_analysis",
                "node_key": "fetch_questions",
                "executor_id": "local-default",
            }
        ],
        node_limits=[
            {
                "pipeline_key": "reading_analysis",
                "node_key": "fetch_questions",
                "concurrency_limit": 2,
            }
        ],
    )
    assert result["workspace"]["name"] == "Reading"
    assert result["settings"]["pipelineKey"] == "reading_analysis"
    assert result["executor_configuration"]["allocations"][0]["concurrency_limit"] == 4
    assert result["executor_configuration"]["bindings"][0]["node_key"] == "fetch_questions"
    assert result["executor_configuration"]["node_limits"][0]["concurrency_limit"] == 2


def test_replace_configuration_rolls_back_workspace_on_invalid_binding(
    workspace_service: WorkspaceConfigurationService,
    workspace,
) -> None:
    original_name = workspace["name"]
    with pytest.raises(InvalidOperationError):
        workspace_service.replace_configuration(
            workspace["id"],
            workspace_patch={"name": "Must Roll Back"},
            settings_patch={"pipelineKey": "reading_analysis"},
            executor_allocations=[{"executor_id": "local-default", "concurrency_limit": 4}],
            node_bindings=[
                {
                    "pipeline_key": "reading_analysis",
                    "node_key": "unknown_node",
                    "executor_id": "local-default",
                }
            ],
            node_limits=[],
        )
    persisted = workspace_service.get(workspace["id"])
    assert persisted["name"] == original_name
    config = workspace_service.job_db.get_workspace_executor_configuration(workspace["id"])
    assert config["allocations"] == []
    assert config["bindings"] == []
    assert config["node_limits"] == []


def test_workspace_configuration_update_delegates(workspace_service, workspace):
    updated = workspace_service.update(workspace["id"], {"description": "New description"})
    assert updated["description"] == "New description"


def test_workspace_configuration_settings_payload(workspace_service, workspace):
    payload = workspace_service.settings_payload(workspace["id"])
    assert payload["pipelineKey"] == "question_content"
