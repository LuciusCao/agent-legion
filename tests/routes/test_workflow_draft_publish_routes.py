from fastapi.testclient import TestClient

from server.app.main import create_app
from server.app.services.node_codes import NodeCodeService
from tests.helpers.auth import authenticate_client

_DRAFT_YAML = """
key: test_publish_flow
label: Test Publish Flow
nodes:
  do_thing:
    capability: do_thing
"""


def _app_and_workspace(tmp_path):
    """Blank-style workspace: row exists, no revision seeded."""
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    workspace = app.state.job_db.create_workspace(
        "Publish WS",
        default_workflow_key="test_publish_flow",
    )
    return app, workspace["id"]


def _publish(client: TestClient, workspace_id: str, definition_yaml: str):
    return client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish",
        json={"definition_yaml": definition_yaml},
    )


def test_publish_rejects_draft_key_mismatch_with_422(tmp_path):
    """Publish enforces draft.key == workspace.default_workflow_key (堵缺口:
    compare already rejects foreign keys, publish previously did not)."""
    app, workspace_id = _app_and_workspace(tmp_path)
    foreign_yaml = _DRAFT_YAML.replace("test_publish_flow", "foreign_flow")

    with authenticate_client(TestClient(app)) as client:
        response = _publish(client, workspace_id, foreign_yaml)

    assert response.status_code == 422
    assert "foreign_flow" in response.json()["detail"]
    assert "test_publish_flow" in response.json()["detail"]
    assert app.state.job_db.get_active_workflow_revision(workspace_id, "test_publish_flow") is None


def test_publish_key_mismatch_422_does_not_shadow_structural_errors(tmp_path):
    """Unparseable YAML falls through to the regular draft-error response."""
    app, workspace_id = _app_and_workspace(tmp_path)

    with authenticate_client(TestClient(app)) as client:
        response = _publish(client, workspace_id, "key: only-key\n")

    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert response.json()["errors"]


def test_publish_unknown_workspace_returns_404(tmp_path):
    app, _ = _app_and_workspace(tmp_path)

    with authenticate_client(TestClient(app)) as client:
        response = _publish(client, "no_such_workspace", _DRAFT_YAML)

    assert response.status_code == 404


def test_publish_first_revision_for_blank_workspace(tmp_path):
    """End of the blank flow: a workspace without any revision publishes v1
    once the draft key matches and the capability resolves."""
    app, workspace_id = _app_and_workspace(tmp_path)
    codes = NodeCodeService(app.state.job_db.path)
    codes.save_draft(
        workspace_id,
        "test_publish_flow",
        "do_thing",
        "def run(job, job_dir, runtime):\n    pass\n",
        "test seed",
    )
    codes.publish(workspace_id, "test_publish_flow", "do_thing")

    with authenticate_client(TestClient(app)) as client:
        response = _publish(client, workspace_id, _DRAFT_YAML)
        active = client.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")

    assert response.status_code == 200
    assert response.json() == {"valid": True, "errors": []}
    assert active.status_code == 200
    assert active.json()["revision"]["version"] == 1
