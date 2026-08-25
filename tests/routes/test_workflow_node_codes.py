"""Workspace-scoped custom node code routes (EXEC-CODE-002)."""

from __future__ import annotations

import pytest

from server.app.services.demo_node_seed import seed_demo_workspace_node_codes
from server.app.services.workflow_revisions import WorkflowRevisionService
from tests.helpers import load_builtin_definition

WF = "education_video_problems_generation"
NODE = "intake_knowledge_points"
BASE = f"/api/workspaces/default/workflows/{WF}/nodes/{NODE}/code"
CUSTOM_V1 = "def run(job, job_dir, runtime):\n    return 'v1'\n"
CUSTOM_V2 = "def run(job, job_dir, runtime):\n    return 'v2'\n"


@pytest.fixture
def workspace_with_revision(client, job_db, settings):
    job_db.create_workspace("default", default_workflow_key=WF)
    definition = load_builtin_definition(WF)
    seed_demo_workspace_node_codes(settings, "default")
    WorkflowRevisionService(job_db).ensure_active_revision("default", definition)
    return client


def test_get_builtin_code(workspace_with_revision) -> None:
    response = workspace_with_revision.get(BASE)

    assert response.status_code == 200
    body = response.json()
    assert body["origin"] == "builtin"
    assert "path" not in body
    # origin=builtin is a system-seeded version inside this workspace.
    assert "def run(" in body["code"]
    assert body["version"] is None
    assert body["has_draft"] is False


def test_draft_publish_get_flow(workspace_with_revision) -> None:
    draft = workspace_with_revision.put(BASE, json={"code": CUSTOM_V1, "change_note": "v1"})
    assert draft.status_code == 200
    body = draft.json()
    assert body["version"] == 2
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
    assert effective["version"] == 2


def test_versions_and_rollback(workspace_with_revision) -> None:
    workspace_with_revision.put(BASE, json={"code": CUSTOM_V1})
    workspace_with_revision.post(f"{BASE}/publish")
    workspace_with_revision.put(BASE, json={"code": CUSTOM_V2})
    workspace_with_revision.post(f"{BASE}/publish")

    versions = workspace_with_revision.get(f"{BASE}/versions").json()["versions"]
    assert [row["version"] for row in versions] == [3, 2, 1]
    assert {row["version"]: row["status"] for row in versions} == {
        1: "archived",
        2: "archived",
        3: "published",
    }
    # The list stays lean: no code text in version summaries.
    assert "code" not in versions[0]

    rolled = workspace_with_revision.post(f"{BASE}/rollback", json={"version": 2})
    assert rolled.status_code == 200
    assert rolled.json()["version"] == 4
    assert rolled.json()["code"] == CUSTOM_V1
    assert workspace_with_revision.get(BASE).json()["code"] == CUSTOM_V1


def test_delete_archives_workspace_code_without_global_fallback(workspace_with_revision) -> None:
    workspace_with_revision.put(BASE, json={"code": CUSTOM_V1})
    workspace_with_revision.post(f"{BASE}/publish")

    deleted = workspace_with_revision.delete(BASE)

    assert deleted.status_code == 200
    assert deleted.json()["archived"] == 1
    assert workspace_with_revision.get(BASE).json()["origin"] == "none"


def test_publish_without_draft_is_404(workspace_with_revision) -> None:
    assert workspace_with_revision.post(f"{BASE}/publish").status_code == 404


def test_rollback_unknown_version_is_404(workspace_with_revision) -> None:
    response = workspace_with_revision.post(f"{BASE}/rollback", json={"version": 99})
    assert response.status_code == 404


@pytest.mark.parametrize("code", ["def run(:\n", "X = 1\n"])
def test_invalid_code_is_400(workspace_with_revision, code) -> None:
    assert workspace_with_revision.put(BASE, json={"code": code}).status_code == 400


def test_unknown_node_reads_as_empty_state(workspace_with_revision) -> None:
    # Draft-only nodes (entering with the next publish) are readable so their
    # code can be drafted before the revision exists: nothing stored yet.
    url = f"/api/workspaces/default/workflows/{WF}/nodes/no_such_node/code"
    response = workspace_with_revision.get(url)
    assert response.status_code == 200
    body = response.json()
    assert body["origin"] == "none"
    assert body["has_draft"] is False


def test_unknown_node_accepts_draft(workspace_with_revision) -> None:
    url = f"/api/workspaces/default/workflows/{WF}/nodes/no_such_node/code"
    assert workspace_with_revision.put(url, json={"code": CUSTOM_V1}).status_code == 200
    body = workspace_with_revision.get(url).json()
    assert body["origin"] == "none"
    assert body["has_draft"] is True
    assert body["draft_code"] == CUSTOM_V1


def test_start_node_is_404(workspace_with_revision) -> None:
    # The synthetic `_start` entry node never executes: no code to read or
    # draft (404 even though draft-only unknown nodes are allowed through).
    url = f"/api/workspaces/default/workflows/{WF}/nodes/_start/code"
    assert workspace_with_revision.get(url).status_code == 404
    assert workspace_with_revision.put(url, json={"code": CUSTOM_V1}).status_code == 404


def test_gate_disabled_is_403(workspace_with_revision, monkeypatch) -> None:
    # client is the worker-session shared app: monkeypatch restores the flag
    # after the test instead of leaking custom_nodes_enabled=False into it.
    monkeypatch.setattr(
        workspace_with_revision.app.state.settings.executor_runtime.workflows,
        "custom_nodes_enabled",
        False,
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
    assert body["draft_version"] == 2


def test_get_version_returns_code_for_any_status(workspace_with_revision) -> None:
    workspace_with_revision.put(BASE, json={"code": CUSTOM_V1})
    workspace_with_revision.post(f"{BASE}/publish")
    workspace_with_revision.put(BASE, json={"code": CUSTOM_V2})
    workspace_with_revision.post(f"{BASE}/publish")

    # Archived user version remains readable (v1 is the factory seed).
    v2 = workspace_with_revision.get(f"{BASE}/versions/2")
    assert v2.status_code == 200
    assert v2.json()["code"] == CUSTOM_V1
    assert v2.json()["status"] == "archived"

    assert workspace_with_revision.get(f"{BASE}/versions/99").status_code == 404


def test_anonymous_version_detail_rejected(anon_client) -> None:
    assert anon_client.get(f"{BASE}/versions/1").status_code == 401


def test_non_admin_member_gets_403(workspace_with_revision, client, job_db) -> None:
    """Node code routes are part of the Studio authoring surface (P4): a
    workspace member — even an editor — gets 403 on reads and writes alike."""
    response = client.post(
        "/api/users",
        json={"username": "viewer1", "password": "pw1"},
        headers={"x-agent-legion-request": "1"},
    )
    assert response.status_code == 201, response.text
    job_db.upsert_workspace_member("default", response.json()["id"], "editor")
    viewer = client.__class__(client.app)
    viewer.post("/api/auth/login", json={"username": "viewer1", "password": "pw1"})
    viewer.headers["x-agent-legion-request"] = "1"

    assert viewer.get(BASE).status_code == 403
    assert viewer.get(f"{BASE}/versions").status_code == 403
    assert viewer.put(BASE, json={"code": CUSTOM_V1}).status_code == 403
    assert viewer.post(f"{BASE}/publish").status_code == 403
    assert viewer.post(f"{BASE}/rollback", json={"version": 1}).status_code == 403
    assert viewer.delete(BASE).status_code == 403


def test_node_code_template_endpoint(client) -> None:
    response = client.get("/api/workflow-node-code-template")

    assert response.status_code == 200
    code = response.json()["code"]
    assert "from workspace_libs.node_sdk import NodeContext, entrypoint" in code
    assert "@entrypoint" in code
    assert "def run(ctx: NodeContext)" in code
    # The template must stay directly runnable as a node module.
    compile(code, "<template>", "exec")


def test_get_code_pathless_capability_returns_none_origin(client_factory, job_db) -> None:
    from server.app.services.workflow_drafts import workflow_definition_from_yaml_string

    # P-0.5: a capability needs no executor definition at all — a node
    # without any published code simply reports origin "none".
    with client_factory(fresh=True) as client:
        job_db.create_workspace("default", default_workflow_key="custom_wf")
        definition = workflow_definition_from_yaml_string(
            "key: custom_wf\nlabel: Custom\nnodes:\n  do_custom:\n    capability: custom_only\n"
        )
        WorkflowRevisionService(job_db).ensure_active_revision("default", definition)

        response = client.get("/api/workspaces/default/workflows/custom_wf/nodes/do_custom/code")

    assert response.status_code == 200
    body = response.json()
    assert body["origin"] == "none"
    assert body["code"] == ""
    assert "path" not in body
