"""M47: runtime 通道端点——PATCH workflow-runtime / GET versions / POST rollback。"""

from __future__ import annotations

import json

import yaml

from server.app.services.workflow_revision_format import definition_to_yaml
from tests.helpers import load_builtin_definition, publish_builtin_revision

_WORKSPACE = "education_video_problems_generation"


def _workspace(job_db) -> str:
    workspace = job_db.create_workspace("Runtime WS", workspace_id=_WORKSPACE)
    publish_builtin_revision(job_db, str(workspace["id"]))
    return str(workspace["id"])


def _draft_yaml(**node_execution: str) -> str:
    definition = load_builtin_definition(_WORKSPACE)
    raw = yaml.safe_load(definition_to_yaml(definition))
    if node_execution:
        raw["nodes"]["write_script"]["execution"] = dict(node_execution)
    return yaml.safe_dump(raw, allow_unicode=True)


def _active_model(job_db, workspace_id: str) -> str:
    active = job_db.get_active_workflow_revision(workspace_id, workspace_id)
    return json.loads(str(active["definition_json"]))["nodes"]["write_script"]["execution"]["model"]


def test_patch_runtime_updates_in_place_and_appends_version(client, job_db) -> None:
    workspace_id = _workspace(job_db)
    before = job_db.get_active_workflow_revision(workspace_id, workspace_id)

    response = client.patch(
        f"/api/workspaces/{workspace_id}/workflow-runtime",
        json={"definition_yaml": _draft_yaml(model="m9")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["revision"]["id"] == before["id"]
    assert payload["revision"]["version"] == before["version"]
    assert payload["runtime_version"]["version"] == 1
    assert payload["runtime_version"]["changed_keys"] == ["nodes.write_script.model"]
    assert payload["runtime_version"]["created_by"].startswith("user:")
    assert _active_model(job_db, workspace_id) == "m9"


def test_patch_runtime_no_change_returns_null_version(client, job_db) -> None:
    workspace_id = _workspace(job_db)

    response = client.patch(
        f"/api/workspaces/{workspace_id}/workflow-runtime",
        json={"definition_yaml": _draft_yaml()},
    )

    assert response.status_code == 200, response.text
    assert response.json()["runtime_version"] is None


def test_patch_runtime_refuses_structural_change(client, job_db) -> None:
    workspace_id = _workspace(job_db)
    raw = yaml.safe_load(_draft_yaml())
    raw["nodes"]["write_script"]["outputs"] = ["result.json", "extra.json"]

    response = client.patch(
        f"/api/workspaces/{workspace_id}/workflow-runtime",
        json={"definition_yaml": yaml.safe_dump(raw, allow_unicode=True)},
    )

    assert response.status_code == 409
    assert job_db.list_runtime_config_versions(workspace_id) == []


def test_patch_runtime_rejects_invalid_yaml(client, job_db) -> None:
    workspace_id = _workspace(job_db)

    response = client.patch(
        f"/api/workspaces/{workspace_id}/workflow-runtime",
        json={"definition_yaml": "nodes: [broken"},
    )

    assert response.status_code == 422


def test_patch_runtime_rejects_foreign_workflow_key(client, job_db) -> None:
    workspace_id = _workspace(job_db)
    foreign = _draft_yaml().replace(_WORKSPACE, "foreign_flow", 1)

    response = client.patch(
        f"/api/workspaces/{workspace_id}/workflow-runtime",
        json={"definition_yaml": foreign},
    )

    assert response.status_code == 422


def test_patch_runtime_requires_active_revision(client, job_db) -> None:
    workspace = job_db.create_workspace("Blank WS", workspace_id=_WORKSPACE)

    response = client.patch(
        f"/api/workspaces/{workspace['id']}/workflow-runtime",
        json={"definition_yaml": _draft_yaml(model="m9")},
    )

    assert response.status_code == 404


def test_list_runtime_versions_newest_first(client, job_db) -> None:
    workspace_id = _workspace(job_db)
    for model in ("m2", "m3"):
        response = client.patch(
            f"/api/workspaces/{workspace_id}/workflow-runtime",
            json={"definition_yaml": _draft_yaml(model=model)},
        )
        assert response.status_code == 200, response.text

    response = client.get(f"/api/workspaces/{workspace_id}/workflow-runtime/versions")

    assert response.status_code == 200, response.text
    versions = response.json()["versions"]
    assert [row["version"] for row in versions] == [2, 1]
    assert versions[0]["changed_keys"] == ["nodes.write_script.model"]
    assert versions[0]["created_at"]


def test_list_runtime_versions_empty_for_unknown_workspace(client) -> None:
    response = client.get("/api/workspaces/no_such_ws/workflow-runtime/versions")

    assert response.status_code == 200
    assert response.json()["versions"] == []


def test_rollback_restores_target_config_as_new_version(client, job_db) -> None:
    workspace_id = _workspace(job_db)
    for model in ("m2", "m3"):
        response = client.patch(
            f"/api/workspaces/{workspace_id}/workflow-runtime",
            json={"definition_yaml": _draft_yaml(model=model)},
        )
        assert response.status_code == 200, response.text

    response = client.post(
        f"/api/workspaces/{workspace_id}/workflow-runtime/rollback", json={"version": 1}
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["runtime_version"]["version"] == 3
    assert payload["runtime_version"]["changed_keys"] == ["nodes.write_script.model"]
    assert _active_model(job_db, workspace_id) == "m2"


def test_rollback_unknown_version_gets_404(client, job_db) -> None:
    workspace_id = _workspace(job_db)

    response = client.post(
        f"/api/workspaces/{workspace_id}/workflow-runtime/rollback", json={"version": 99}
    )

    assert response.status_code == 404


def test_runtime_channel_requires_auth(anon_client, job_db) -> None:
    workspace_id = _workspace(job_db)

    response = anon_client.patch(
        f"/api/workspaces/{workspace_id}/workflow-runtime",
        json={"definition_yaml": _draft_yaml(model="m9")},
    )

    assert response.status_code == 401
    assert job_db.list_runtime_config_versions(workspace_id) == []
