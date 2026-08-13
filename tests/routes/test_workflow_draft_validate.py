"""workflow-drafts/validate returns the full publish validation set."""

from __future__ import annotations

_DRAFT_YAML = """
key: test_validate_flow
label: Test Validate Flow
nodes:
  clean_and_parse:
    capability: clean_and_parse
"""


def _urls(workspace_id: str) -> tuple[str, str]:
    base = f"/api/workspaces/{workspace_id}/workflow-drafts"
    return f"{base}/validate", f"{base}/publish"


def test_validate_reports_binding_errors(client, job_db) -> None:
    job_db.create_workspace("ws-validate", default_workflow_key="test_validate_flow")
    validate_url, publish_url = _urls("ws-validate")

    response = client.post(validate_url, json={"definition_yaml": _DRAFT_YAML})

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert any("missing executor binding" in error for error in body["errors"])

    # The same set publish would report — and validate persisted nothing.
    publish = client.post(publish_url, json={"definition_yaml": _DRAFT_YAML})
    assert publish.json()["errors"] == body["errors"]
    assert job_db.get_active_workflow_revision("ws-validate", "test_validate_flow") is None


def test_validate_clean_with_complete_binding(client, job_db) -> None:
    workspace = job_db.create_workspace("ws-validate-ok", default_workflow_key="test_validate_flow")
    job_db.replace_workspace_executor_configuration(
        workspace["id"],
        allocations=[{"executor_id": "code-default", "concurrency_limit": 1}],
        bindings=[
            {
                "workflow_key": "test_validate_flow",
                "node_key": "clean_and_parse",
                "executor_id": "code-default",
            }
        ],
        node_limits=[],
    )
    validate_url, _ = _urls("ws-validate-ok")

    response = client.post(validate_url, json={"definition_yaml": _DRAFT_YAML})

    assert response.status_code == 200
    assert response.json() == {"valid": True, "errors": []}
