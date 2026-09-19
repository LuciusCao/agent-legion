"""Runs API × text 条目：入口契约、落成材料、dedup、形状校验、存储未配置。"""

from __future__ import annotations

import copy
import hashlib

import pytest

from tests.fakes.storage import FakeObjectStorage

WORKFLOW_KEY = "education_video_problems_generation"

FakeStorage = FakeObjectStorage

REQUIREMENT = "# 歌曲创作需求\n- 参考歌曲：Anti-Hero\n- 新歌语言：中文\n"


@pytest.fixture
def storage(client, monkeypatch) -> FakeStorage:
    fake = FakeStorage()
    monkeypatch.setattr(client.app.state.materials_service, "storage", fake)
    return fake


def _create_workspace(client) -> str:
    response = client.post(
        "/api/workspaces",
        json={"id": WORKFLOW_KEY, "name": "runs-text-ws"},
    )
    assert response.status_code == 200, response.text
    from tests.helpers import publish_builtin_revision

    publish_builtin_revision(client.app.state.job_db, WORKFLOW_KEY)
    return response.json()["workspace"]["id"]


def _accept_text_items(job_db, workspace_id: str) -> None:
    """Republish the demo workflow declaring ``[material, text]``."""
    from server.app.services.workflow_revisions import WorkflowRevisionService
    from server.app.workflows.builtin_demo import DEMO_WORKFLOW_DEFINITION
    from server.app.workflows.definition import workflow_definition_from_dict

    raw = copy.deepcopy(DEMO_WORKFLOW_DEFINITION)
    raw["nodes"]["_start"]["accepted_item_types"] = ["material", "text"]
    WorkflowRevisionService(job_db).publish_workspace_revision(
        workspace_id, workflow_definition_from_dict(raw)
    )


def _create_run(client, workspace_id: str, items: list[dict]):
    return client.post(
        f"/api/workspaces/{workspace_id}/runs",
        json={"workflow_key": WORKFLOW_KEY, "items": items},
    )


def _materials(client, workspace_id: str) -> list[dict]:
    return client.get(f"/api/workspaces/{workspace_id}/materials").json()["materials"]


def test_text_item_rejected_by_default_contract(client, storage, job_db) -> None:
    """Seeded demo revision accepts materials only: text is opt-in, and the
    rejection happens before the material is written (no row, no object)."""
    workspace_id = _create_workspace(client)

    response = _create_run(client, workspace_id, [{"type": "text", "content": REQUIREMENT}])

    assert response.status_code == 400
    assert "not accepted by this workflow" in response.json()["detail"]
    assert client.get(f"/api/workspaces/{workspace_id}/runs").json()["runs"] == []
    assert _materials(client, workspace_id) == []
    assert storage.objects == {}


def test_text_item_becomes_ready_material_and_job(client, storage, job_db) -> None:
    workspace_id = _create_workspace(client)
    _accept_text_items(job_db, workspace_id)

    response = _create_run(
        client,
        workspace_id,
        [{"type": "text", "content": REQUIREMENT, "filename": "创作需求.md"}],
    )

    assert response.status_code == 200, response.text
    assert response.json()["created_count"] == 1
    digest = hashlib.sha256(REQUIREMENT.encode("utf-8")).hexdigest()
    # Object first, row second: the ready row points at the stored bytes.
    assert storage.objects[f"{workspace_id}/{digest}/创作需求.md"] == REQUIREMENT.encode("utf-8")
    (material,) = _materials(client, workspace_id)
    assert material["status"] == "ready"
    assert material["filename"] == "创作需求.md"
    assert material["content_hash"] == digest
    assert material["content_type"] == "text/markdown; charset=utf-8"
    assert material["size_bytes"] == len(REQUIREMENT.encode("utf-8"))
    # Downstream sees an ordinary material job (input_json / source columns).
    (job,) = client.get(f"/api/workspaces/{workspace_id}/jobs").json()["jobs"]
    assert job["source_type"] == "material"
    assert job["source_id"] == material["id"]
    assert job["title"] == "创作需求.md"
    with job_db.connect() as conn:
        row = conn.execute("select input_json from jobs where id=%s", (job["id"],)).fetchone()
    assert '"type": "material"' in str(row["input_json"])
    assert material["id"] in str(row["input_json"])


def test_text_item_default_filename_and_dedup(client, storage, job_db) -> None:
    workspace_id = _create_workspace(client)
    _accept_text_items(job_db, workspace_id)

    first = _create_run(client, workspace_id, [{"type": "text", "content": REQUIREMENT}])
    assert first.status_code == 200, first.text
    (material,) = _materials(client, workspace_id)
    assert material["filename"] == "需求.md"

    # Identical text → same content-addressed material → same job dedup key.
    second = _create_run(client, workspace_id, [{"type": "text", "content": REQUIREMENT}])
    assert second.status_code == 400
    assert "No tasks were resolved" in second.json()["detail"]
    assert len(_materials(client, workspace_id)) == 1
    # Different text is a new material and a new job.
    third = _create_run(client, workspace_id, [{"type": "text", "content": REQUIREMENT + "x"}])
    assert third.status_code == 200, third.text
    assert len(_materials(client, workspace_id)) == 2


def test_text_item_revives_stale_row_with_same_hash(client, storage, job_db) -> None:
    """An abandoned presign of the same bytes (uploading row, no object) is
    re-pointed at the freshly written object instead of failing 'not ready'."""
    workspace_id = _create_workspace(client)
    _accept_text_items(job_db, workspace_id)
    digest = hashlib.sha256(REQUIREMENT.encode("utf-8")).hexdigest()
    presign = client.post(
        f"/api/workspaces/{workspace_id}/materials/presign",
        json={
            "filename": "old.txt",
            "size_bytes": 1,
            "content_type": "text/plain",
            "content_hash": digest,
        },
    )
    assert presign.status_code == 200, presign.text
    stale_id = presign.json()["material"]["id"]

    response = _create_run(client, workspace_id, [{"type": "text", "content": REQUIREMENT}])

    assert response.status_code == 200, response.text
    (material,) = _materials(client, workspace_id)
    assert material["id"] == stale_id
    assert material["status"] == "ready"
    assert material["filename"] == "需求.md"
    assert storage.objects[f"{workspace_id}/{digest}/需求.md"] == REQUIREMENT.encode("utf-8")


@pytest.mark.parametrize(
    ("item", "detail"),
    [
        ({"type": "text", "content": "   \n"}, "non-empty content"),
        ({"type": "text", "content": "x", "filename": "../需求.md"}, "invalid"),
        ({"type": "text", "content": "x", "filename": "需求.exe"}, ".md or .txt"),
        # 30k CJK chars pass the contract's character cap but exceed 64 KiB of UTF-8.
        ({"type": "text", "content": "需" * 30000}, "exceeds"),
    ],
)
def test_text_item_shape_errors_write_nothing(client, storage, job_db, item, detail) -> None:
    workspace_id = _create_workspace(client)
    _accept_text_items(job_db, workspace_id)

    response = _create_run(client, workspace_id, [item])

    assert response.status_code == 400, response.text
    assert detail in response.json()["detail"]
    assert _materials(client, workspace_id) == []
    assert storage.objects == {}


def test_text_item_without_storage_returns_503(client, job_db, monkeypatch) -> None:
    workspace_id = _create_workspace(client)
    _accept_text_items(job_db, workspace_id)
    monkeypatch.setattr(client.app.state.materials_service, "storage", None)

    response = _create_run(client, workspace_id, [{"type": "text", "content": REQUIREMENT}])

    assert response.status_code == 503
    assert client.get(f"/api/workspaces/{workspace_id}/runs").json()["runs"] == []
