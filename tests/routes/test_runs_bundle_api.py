"""Runs API × bundle 条目（#156）：入口契约、candidate 校验、dedup。"""

from __future__ import annotations

import copy
import hashlib
import io

import pytest

from server.app.storage import ObjectHead

WORKFLOW_KEY = "education_video_problems_generation"


class FakeStorage:
    """In-memory ObjectStorage test double; never touches the network."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def presign_put(self, storage_key: str, size_bytes: int, expires_seconds: int = 3600) -> str:
        return f"https://s3.test/upload/{storage_key}"

    def head_object(self, storage_key: str) -> ObjectHead | None:
        payload = self.objects.get(storage_key)
        return None if payload is None else ObjectHead(size_bytes=len(payload))

    def open_stream(self, storage_key: str) -> io.BytesIO:
        return io.BytesIO(self.objects[storage_key])

    def delete_object(self, storage_key: str) -> None:
        self.objects.pop(storage_key, None)


@pytest.fixture
def storage(client, monkeypatch) -> FakeStorage:
    fake = FakeStorage()
    monkeypatch.setattr(client.app.state.materials_service, "storage", fake)
    return fake


def _create_workspace(client) -> str:
    response = client.post(
        "/api/workspaces",
        json={"id": WORKFLOW_KEY, "name": "runs-bundle-ws"},
    )
    assert response.status_code == 200, response.text
    # v62: creation seeds nothing; the bundle tests republish their own
    # contract variants, which requires a base active revision.
    from tests.helpers import publish_builtin_revision

    publish_builtin_revision(client.app.state.job_db, WORKFLOW_KEY)
    return response.json()["workspace"]["id"]


def _accept_bundle_items(job_db, workspace_id: str) -> None:
    """Republish the demo workflow declaring ``[material, bundle]`` (#156)."""
    from server.app.services.workflow_revisions import WorkflowRevisionService
    from server.app.workflows.builtin_demo import DEMO_WORKFLOW_DEFINITION
    from server.app.workflows.definition import workflow_definition_from_dict

    raw = copy.deepcopy(DEMO_WORKFLOW_DEFINITION)
    raw["nodes"]["_start"]["accepted_item_types"] = ["material", "bundle"]
    WorkflowRevisionService(job_db).publish_workspace_revision(
        workspace_id, workflow_definition_from_dict(raw)
    )


def _ready_bundle(client, storage: FakeStorage, workspace_id: str) -> str:
    payload = b"run-bundle-member"
    content_hash = hashlib.sha256(payload).hexdigest()
    response = client.post(
        f"/api/workspaces/{workspace_id}/materials/presign",
        json={
            "filename": "a.txt",
            "size_bytes": len(payload),
            "content_type": "text/plain",
            "content_hash": content_hash,
        },
    )
    assert response.status_code == 200, response.text
    material_id = response.json()["material"]["id"]
    storage.objects[f"{workspace_id}/{content_hash}/a.txt"] = payload
    assert (
        client.post(f"/api/workspaces/{workspace_id}/materials/{material_id}/complete").status_code
        == 200
    )
    response = client.post(
        f"/api/workspaces/{workspace_id}/material-bundles",
        json={"name": "folder", "members": [{"material_id": material_id, "path": "a.txt"}]},
    )
    assert response.status_code == 200, response.text
    return response.json()["bundle"]["id"]


def _create_run(client, workspace_id: str, items: list[dict]):
    return client.post(
        f"/api/workspaces/{workspace_id}/runs",
        json={"workflow_key": WORKFLOW_KEY, "items": items},
    )


def test_bundle_item_rejected_by_default_contract(client, storage, job_db) -> None:
    """Seeded demo revision accepts materials only: bundle items get a 400
    before any write — ``bundle`` is opt-in (#156, EXEC-WORKFLOW-START-001)."""
    workspace_id = _create_workspace(client)
    bundle_id = _ready_bundle(client, storage, workspace_id)

    response = _create_run(client, workspace_id, [{"type": "bundle", "bundle_id": bundle_id}])

    assert response.status_code == 400
    assert "not accepted by this workflow" in response.json()["detail"]
    assert client.get(f"/api/workspaces/{workspace_id}/runs").json()["runs"] == []


def test_bundle_item_accepted_when_contract_declares_it(client, storage, job_db) -> None:
    workspace_id = _create_workspace(client)
    bundle_id = _ready_bundle(client, storage, workspace_id)
    _accept_bundle_items(job_db, workspace_id)

    response = _create_run(client, workspace_id, [{"type": "bundle", "bundle_id": bundle_id}])

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["created_count"] == 1
    job = payload["jobs"][0]
    assert job["source_type"] == "bundle"
    assert job["source_id"] == bundle_id

    # (entity_type, entity_id) dedup：同一 bundle 重提交解析为零。
    second = _create_run(client, workspace_id, [{"type": "bundle", "bundle_id": bundle_id}])
    assert second.status_code == 400
    assert "No tasks were resolved" in second.json()["detail"]


def test_bundle_item_unknown_bundle_returns_404(client, storage, job_db) -> None:
    workspace_id = _create_workspace(client)
    _accept_bundle_items(job_db, workspace_id)

    response = _create_run(client, workspace_id, [{"type": "bundle", "bundle_id": "b-missing"}])

    assert response.status_code == 404


def test_bundle_item_with_unready_member_returns_400(client, storage, job_db) -> None:
    workspace_id = _create_workspace(client)
    bundle_id = _ready_bundle(client, storage, workspace_id)
    _accept_bundle_items(job_db, workspace_id)
    with job_db.connect() as conn:
        conn.execute("update materials set status='expired' where workspace_id=%s", (workspace_id,))

    response = _create_run(client, workspace_id, [{"type": "bundle", "bundle_id": bundle_id}])

    assert response.status_code == 400
    assert "not fully ready" in response.json()["detail"]
    assert client.get(f"/api/workspaces/{workspace_id}/runs").json()["runs"] == []
