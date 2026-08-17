"""workflow-drafts/validate returns the full publish validation set."""

from __future__ import annotations

from server.app.services.node_codes import NodeCodeService
from tests.postgres_support import TEST_DATABASE_URL

_DRAFT_YAML = """
key: test_validate_flow
label: Test Validate Flow
nodes:
  publish_content:
    capability: publish_content
"""


def _urls(workspace_id: str) -> tuple[str, str]:
    base = f"/api/workspaces/{workspace_id}/workflow-drafts"
    return f"{base}/validate", f"{base}/publish"


def test_validate_reports_unresolvable_code_errors(client, job_db) -> None:
    job_db.create_workspace("ws-validate", default_workflow_key="test_validate_flow")
    validate_url, publish_url = _urls("ws-validate")

    response = client.post(validate_url, json={"definition_yaml": _DRAFT_YAML})

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    # P-0.5: the publish gate is resolvable node code, not executor bindings.
    assert any("no published node code" in error for error in body["errors"])

    # The same set publish would report — and validate persisted nothing.
    publish = client.post(publish_url, json={"definition_yaml": _DRAFT_YAML})
    assert publish.json()["errors"] == body["errors"]
    assert job_db.get_active_workflow_revision("ws-validate", "test_validate_flow") is None


def test_validate_clean_with_published_node_code(client, job_db) -> None:
    workspace = job_db.create_workspace("ws-validate-ok", default_workflow_key="test_validate_flow")
    codes = NodeCodeService(TEST_DATABASE_URL)
    codes.save_draft(
        workspace["id"],
        "test_validate_flow",
        "publish_content",
        "def run(job, job_dir, runtime):\n    pass\n",
        "test seed",
    )
    codes.publish(workspace["id"], "test_validate_flow", "publish_content")
    validate_url, _ = _urls("ws-validate-ok")

    response = client.post(validate_url, json={"definition_yaml": _DRAFT_YAML})

    assert response.status_code == 200
    assert response.json() == {"valid": True, "errors": []}
