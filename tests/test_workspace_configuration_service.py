import pytest

from server.app.services.job_errors import NotFoundError
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


def test_workspace_configuration_preserves_atomic_save(workspace_service, workspace):
    result = workspace_service.replace_configuration(
        workspace["id"],
        workspace_patch={"name": "Renamed"},
        settings_patch={"pipelineKey": "question_content"},
        agents=[{"agent_id": "pi", "concurrency_limit": 2}],
    )
    assert result["workspace"]["name"] == "Renamed"
    assert result["agents"][0]["concurrency_limit"] == 2


def test_workspace_configuration_update_delegates(workspace_service, workspace):
    updated = workspace_service.update(workspace["id"], {"description": "New description"})
    assert updated["description"] == "New description"


def test_workspace_configuration_settings_payload(workspace_service, workspace):
    payload = workspace_service.settings_payload(workspace["id"])
    assert payload["pipelineKey"] == "question_content"
