"""Runs API: auth, item-based creation, list and detail."""

from __future__ import annotations

import pytest

WORKFLOW_KEY = "education_video_problems_generation"


_CREATE_COUNT = 0


def _create_workspace(client, name: str = "runs-ws") -> str:
    # v62: id==key, unique per call within a test (TRUNCATE isolation resets
    # the counter); creation seeds nothing, so publish the legacy-intake
    # revision run creation needs.
    global _CREATE_COUNT
    _CREATE_COUNT += 1
    ws_id = WORKFLOW_KEY if _CREATE_COUNT == 1 else f"{WORKFLOW_KEY}_{_CREATE_COUNT}"
    response = client.post(
        "/api/workspaces",
        json={"id": ws_id, "name": name},
    )
    assert response.status_code == 200, response.text
    from tests.helpers import publish_legacy_intake_revision

    publish_legacy_intake_revision(client.app.state.job_db, ws_id)
    return response.json()["workspace"]["id"]


@pytest.fixture(autouse=True)
def _reset_create_count():
    global _CREATE_COUNT
    _CREATE_COUNT = 0
    yield


def _insert_material(job_db, workspace_id: str, material_id: str, *, status: str = "ready") -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into materials(id, workspace_id, content_hash, filename, content_type,"
            " size_bytes, storage_key, status, created_by)"
            " values (%s, %s, %s, 'doc.txt', 'text/plain', 10, %s, %s, 'tester')",
            (
                material_id,
                workspace_id,
                f"hash-{material_id}",
                f"{workspace_id}/hash-{material_id}/doc.txt",
                status,
            ),
        )


def _insert_connection(job_db, key: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into external_connections(key, type, display_name, config_json)"
            " values (%s, 'hmac_token', %s, '{}')",
            (key, key),
        )


def _accept_all_item_types(job_db, workspace_id: str) -> None:
    """Republish the demo workflow with a widened start-node entry contract.

    The seeded demo revision accepts materials only; tests exercising ref
    items publish this variant (same DAG, ``accepted_item_types: [material,
    ref]``) so the entry contract lets them through.
    """
    import copy

    from server.app.services.workflow_revisions import WorkflowRevisionService
    from server.app.workflows.builtin_demo import DEMO_WORKFLOW_DEFINITION
    from server.app.workflows.definition import workflow_definition_from_dict

    raw = copy.deepcopy(DEMO_WORKFLOW_DEFINITION)
    raw["nodes"]["_start"]["accepted_item_types"] = ["material", "ref"]
    WorkflowRevisionService(job_db).publish_workspace_revision(
        workspace_id, workflow_definition_from_dict(raw)
    )


def _create_run(client, workspace_id: str, items: list[dict]):
    return client.post(
        f"/api/workspaces/{workspace_id}/runs",
        json={"workflow_key": WORKFLOW_KEY, "items": items},
    )


def test_runs_require_auth(anon_client) -> None:
    url = "/api/workspaces/ws-1/runs"
    assert anon_client.get(url).status_code == 401
    assert anon_client.get(f"{url}/run-1").status_code == 401
    assert (
        anon_client.post(
            url,
            json={"workflow_key": "wf", "items": [{"type": "material", "material_id": "m"}]},
        ).status_code
        == 401
    )


def test_non_member_gets_404(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    response = client.post(
        "/api/users",
        json={"username": "runs-member", "password": "pw1"},
    )
    assert response.status_code == 201, response.text
    member = client.__class__(client.app)
    response = member.post("/api/auth/login", json={"username": "runs-member", "password": "pw1"})
    assert response.status_code == 200, response.text
    member.headers["x-agent-legion-request"] = "1"

    assert member.get(f"/api/workspaces/{workspace_id}/runs").status_code == 404
    assert member.get(f"/api/workspaces/{workspace_id}/runs/run-1").status_code == 404
    response = member.post(
        f"/api/workspaces/{workspace_id}/runs",
        json={"workflow_key": WORKFLOW_KEY, "items": [{"type": "material", "material_id": "m"}]},
    )
    assert response.status_code == 404


def test_create_list_and_detail(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    _accept_all_item_types(job_db, workspace_id)
    _insert_material(job_db, workspace_id, "mat-1")
    _insert_connection(job_db, "cms-main")

    response = _create_run(
        client,
        workspace_id,
        [
            {"type": "material", "material_id": "mat-1"},
            {"type": "ref", "connection_key": "cms-main", "external_id": "Q-1"},
        ],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created_count"] == 2
    run = body["run"]
    assert run["source_kind"] == "items"
    assert run["status"] == "created"
    assert run["created_count"] == 2
    titles = {job["title"] for job in body["jobs"]}
    assert titles == {"doc.txt", "Q-1"}
    assert all(job["batch_id"] == run["id"] for job in body["jobs"])

    listing = client.get(f"/api/workspaces/{workspace_id}/runs")
    assert listing.status_code == 200, listing.text
    assert [record["id"] for record in listing.json()["runs"]] == [run["id"]]

    detail = client.get(f"/api/workspaces/{workspace_id}/runs/{run['id']}")
    assert detail.status_code == 200, detail.text
    detail_body = detail.json()
    assert detail_body["run"]["id"] == run["id"]
    assert detail_body["run"]["created_count"] == 2
    assert detail_body["job_stats"] == {"total": 2, "by_status": {"queued": 2}}


def test_run_detail_is_workspace_scoped(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    other_id = _create_workspace(client, name="runs-ws-other")
    _insert_material(job_db, workspace_id, "mat-1")
    run = _create_run(client, workspace_id, [{"type": "material", "material_id": "mat-1"}]).json()[
        "run"
    ]

    assert client.get(f"/api/workspaces/{other_id}/runs/{run['id']}").status_code == 404
    assert client.get(f"/api/workspaces/{workspace_id}/runs/missing").status_code == 404


def test_material_validation_errors(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    _insert_material(job_db, workspace_id, "mat-uploading", status="uploading")
    other_id = _create_workspace(client, name="runs-ws-foreign")
    _insert_material(job_db, other_id, "mat-foreign")

    response = _create_run(
        client, workspace_id, [{"type": "material", "material_id": "mat-uploading"}]
    )
    assert response.status_code == 400
    assert "not ready" in response.json()["detail"]

    response = _create_run(
        client, workspace_id, [{"type": "material", "material_id": "mat-foreign"}]
    )
    assert response.status_code == 404
    assert "Material not found" in response.json()["detail"]


def test_ref_validation_errors(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    _accept_all_item_types(job_db, workspace_id)

    response = _create_run(
        client,
        workspace_id,
        [{"type": "ref", "connection_key": "missing-conn", "external_id": "Q-1"}],
    )
    assert response.status_code == 400
    assert "Unknown connection key" in response.json()["detail"]

    # Malformed items are rejected by the request contract.
    response = _create_run(client, workspace_id, [{"type": "ref", "connection_key": "c"}])
    assert response.status_code == 422
    response = _create_run(client, workspace_id, [{"type": "unknown", "id": "x"}])
    assert response.status_code == 422
    response = client.post(
        f"/api/workspaces/{workspace_id}/runs",
        json={"workflow_key": WORKFLOW_KEY, "items": []},
    )
    assert response.status_code == 422


def test_duplicate_items_return_400(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    _insert_material(job_db, workspace_id, "mat-1")
    first = _create_run(client, workspace_id, [{"type": "material", "material_id": "mat-1"}])
    assert first.status_code == 200, first.text

    second = _create_run(client, workspace_id, [{"type": "material", "material_id": "mat-1"}])
    assert second.status_code == 400
    assert "No tasks were resolved" in second.json()["detail"]


def test_item_type_rejected_by_start_contract(client, job_db) -> None:
    """The seeded demo revision accepts materials only: ref items get a 400
    before any write (EXEC-WORKFLOW-START-001)."""
    workspace_id = _create_workspace(client)
    _insert_connection(job_db, "cms-main")

    response = _create_run(
        client,
        workspace_id,
        [{"type": "ref", "connection_key": "cms-main", "external_id": "Q-1"}],
    )

    assert response.status_code == 400
    assert "not accepted by this workflow" in response.json()["detail"]
    assert client.get(f"/api/workspaces/{workspace_id}/runs").json()["runs"] == []
