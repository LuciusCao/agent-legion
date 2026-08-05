"""Workspace-scoped custom node code routes (EXEC-CODE-002)."""

from __future__ import annotations

import pytest

from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workflow_revisions import WorkflowRevisionService

WF = "question_comprehension_info"
NODE = "fetch_questions"
BASE = f"/api/workspaces/default/workflows/{WF}/nodes/{NODE}/code"
CUSTOM_V1 = "def run(job, job_dir, runtime):\n    return 'v1'\n"
CUSTOM_V2 = "def run(job, job_dir, runtime):\n    return 'v2'\n"


@pytest.fixture
def workspace_with_revision(client, job_db, settings):
    job_db.create_workspace("default", default_workflow_key=WF)
    definition = WorkflowCatalogService(settings).definition(WF)
    WorkflowRevisionService(job_db).ensure_active_revision("default", definition)
    return client


def test_get_builtin_code(workspace_with_revision) -> None:
    response = workspace_with_revision.get(BASE)

    assert response.status_code == 200
    body = response.json()
    assert body["origin"] == "builtin"
    assert body["path"] == "workflow_nodes/question_intake.py"
    assert "def run(" in body["code"]
    assert body["version"] is None
    assert body["has_draft"] is False


def test_draft_publish_get_flow(workspace_with_revision) -> None:
    draft = workspace_with_revision.put(BASE, json={"code": CUSTOM_V1, "change_note": "v1"})
    assert draft.status_code == 200
    body = draft.json()
    assert body["version"] == 1
    assert body["status"] == "draft"
    assert body["created_by"].startswith("user:")
    assert body["change_note"] == "v1"
    assert len(body["code_hash"]) == 64

    # A draft does not change the effective code yet.
    before = workspace_with_revision.get(BASE).json()
    assert before["origin"] == "builtin"
    assert before["has_draft"] is True

    published = workspace_with_revision.post(f"{BASE}/publish")
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    assert published.json()["published_at"] is not None

    effective = workspace_with_revision.get(BASE).json()
    assert effective["origin"] == "custom"
    assert effective["code"] == CUSTOM_V1
    assert effective["version"] == 1


def test_versions_and_rollback(workspace_with_revision) -> None:
    workspace_with_revision.put(BASE, json={"code": CUSTOM_V1})
    workspace_with_revision.post(f"{BASE}/publish")
    workspace_with_revision.put(BASE, json={"code": CUSTOM_V2})
    workspace_with_revision.post(f"{BASE}/publish")

    versions = workspace_with_revision.get(f"{BASE}/versions").json()["versions"]
    assert [row["version"] for row in versions] == [2, 1]
    assert {row["version"]: row["status"] for row in versions} == {
        1: "archived",
        2: "published",
    }
    # The list stays lean: no code text in version summaries.
    assert "code" not in versions[0]

    rolled = workspace_with_revision.post(f"{BASE}/rollback", json={"version": 1})
    assert rolled.status_code == 200
    assert rolled.json()["version"] == 3
    assert rolled.json()["code"] == CUSTOM_V1
    assert workspace_with_revision.get(BASE).json()["code"] == CUSTOM_V1


def test_delete_archives_and_falls_back_to_builtin(workspace_with_revision) -> None:
    workspace_with_revision.put(BASE, json={"code": CUSTOM_V1})
    workspace_with_revision.post(f"{BASE}/publish")

    deleted = workspace_with_revision.delete(BASE)

    assert deleted.status_code == 200
    assert deleted.json()["archived"] == 1
    assert workspace_with_revision.get(BASE).json()["origin"] == "builtin"


def test_publish_without_draft_is_404(workspace_with_revision) -> None:
    assert workspace_with_revision.post(f"{BASE}/publish").status_code == 404


def test_rollback_unknown_version_is_404(workspace_with_revision) -> None:
    response = workspace_with_revision.post(f"{BASE}/rollback", json={"version": 99})
    assert response.status_code == 404


@pytest.mark.parametrize("code", ["def run(:\n", "X = 1\n"])
def test_invalid_code_is_400(workspace_with_revision, code) -> None:
    assert workspace_with_revision.put(BASE, json={"code": code}).status_code == 400


def test_unknown_node_is_404(workspace_with_revision) -> None:
    url = f"/api/workspaces/default/workflows/{WF}/nodes/no_such_node/code"
    assert workspace_with_revision.get(url).status_code == 404


def test_gate_disabled_is_403(workspace_with_revision) -> None:
    workspace_with_revision.app.state.settings.executor_runtime.workflows.custom_nodes_enabled = (
        False
    )

    assert workspace_with_revision.get(BASE).status_code == 403
    assert workspace_with_revision.put(BASE, json={"code": CUSTOM_V1}).status_code == 403
    assert workspace_with_revision.post(f"{BASE}/publish").status_code == 403
    assert workspace_with_revision.get(f"{BASE}/versions").status_code == 403
    assert workspace_with_revision.post(f"{BASE}/rollback", json={"version": 1}).status_code == 403
    assert workspace_with_revision.delete(BASE).status_code == 403


def test_anonymous_access_rejected(anon_client) -> None:
    assert anon_client.get(BASE).status_code == 401
    assert anon_client.put(BASE, json={"code": CUSTOM_V1}).status_code == 401
    assert anon_client.delete(BASE).status_code == 401


def test_get_returns_draft_content(workspace_with_revision) -> None:
    workspace_with_revision.put(BASE, json={"code": CUSTOM_V1, "change_note": "wip"})

    body = workspace_with_revision.get(BASE).json()

    assert body["origin"] == "builtin"  # not published yet
    assert body["has_draft"] is True
    assert body["draft_code"] == CUSTOM_V1
    assert body["draft_version"] == 1


def test_get_version_returns_code_for_any_status(workspace_with_revision) -> None:
    workspace_with_revision.put(BASE, json={"code": CUSTOM_V1})
    workspace_with_revision.post(f"{BASE}/publish")
    workspace_with_revision.put(BASE, json={"code": CUSTOM_V2})
    workspace_with_revision.post(f"{BASE}/publish")

    # Archived v1 is still readable.
    v1 = workspace_with_revision.get(f"{BASE}/versions/1")
    assert v1.status_code == 200
    assert v1.json()["code"] == CUSTOM_V1
    assert v1.json()["status"] == "archived"

    assert workspace_with_revision.get(f"{BASE}/versions/99").status_code == 404


def test_anonymous_version_detail_rejected(anon_client) -> None:
    assert anon_client.get(f"{BASE}/versions/1").status_code == 401
