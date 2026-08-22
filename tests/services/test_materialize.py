"""Host 物化服务：material_runtime_block / material_claim_block（design §6.2）。"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

from server.app.services.material_cache import (
    is_material_input,
    material_claim_block,
    material_runtime_block,
)
from server.app.storage import ObjectHead
from shared.material_cache import MaterializeError

WORKSPACE_ID = "ws-mat-cache"
PAYLOAD = b"host-side-material" * 50
HASH = hashlib.sha256(PAYLOAD).hexdigest()
MATERIAL_ID = "mat-1"
STORAGE_KEY = f"{WORKSPACE_ID}/{HASH}/notes.txt"


class FakeStorage:
    """In-memory ObjectStorage double（含 presign_get）；不碰网络。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {STORAGE_KEY: PAYLOAD}
        self.opened = 0
        self.presigned_gets: list[str] = []

    def presign_put(self, storage_key: str, size_bytes: int, expires_seconds: int = 3600) -> str:
        return f"https://s3.test/upload/{storage_key}"

    def presign_get(self, storage_key: str, expires_seconds: int = 3600) -> str:
        self.presigned_gets.append(storage_key)
        return f"https://s3.test/download/{storage_key}?sig=fake"

    def head_object(self, storage_key: str) -> ObjectHead | None:
        payload = self.objects.get(storage_key)
        return None if payload is None else ObjectHead(size_bytes=len(payload))

    def open_stream(self, storage_key: str) -> io.BytesIO:
        self.opened += 1
        return io.BytesIO(self.objects[storage_key])

    def delete_object(self, storage_key: str) -> None:
        self.objects.pop(storage_key, None)


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def material(job_db):
    """一个 ready 的 material 行 + 所属 workspace。"""
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values (%s, 'MatCache', 'demo_workflow') on conflict(id) do nothing",
            (WORKSPACE_ID,),
        )
        conn.execute(
            "insert into materials("
            " id, workspace_id, content_hash, filename, content_type,"
            " size_bytes, storage_key, status, created_by"
            ") values (%s, %s, %s, 'notes.txt', 'text/plain', %s, %s, 'ready', 'user-1')",
            (MATERIAL_ID, WORKSPACE_ID, HASH, len(PAYLOAD), STORAGE_KEY),
        )
    return MATERIAL_ID


def _job(input_doc: dict | None) -> dict:
    return {"id": "job-1", "input_json": json.dumps(input_doc) if input_doc else ""}


def test_is_material_input() -> None:
    assert is_material_input(_job({"type": "material", "material_id": "m"}))
    assert not is_material_input(_job({"type": "ref", "external_id": "q-1"}))
    assert not is_material_input(_job(None))
    assert not is_material_input({"input_json": "not-json"})


def test_runtime_block_materializes_and_hits_cache(
    job_db, material, storage, tmp_path: Path
) -> None:
    job = _job({"type": "material", "material_id": material})

    block = material_runtime_block(job_db.path, tmp_path, WORKSPACE_ID, job, storage=storage)

    assert block is not None
    assert block["material_id"] == material
    assert block["filename"] == "notes.txt"
    assert block["content_type"] == "text/plain"
    assert block["content_hash"] == HASH
    path = Path(block["path"])
    assert path == tmp_path / HASH[:2] / HASH / "notes.txt"
    assert path.read_bytes() == PAYLOAD
    assert storage.opened == 1

    again = material_runtime_block(job_db.path, tmp_path, WORKSPACE_ID, job, storage=storage)
    assert again is not None and again["path"] == str(path)
    assert storage.opened == 1, "cache hit must not re-download"


def test_runtime_block_returns_none_for_non_material(job_db, storage, tmp_path: Path) -> None:
    assert (
        material_runtime_block(
            job_db.path,
            tmp_path,
            WORKSPACE_ID,
            _job({"type": "ref", "external_id": "q-1"}),
            storage=storage,
        )
        is None
    )
    assert material_runtime_block(job_db.path, tmp_path, WORKSPACE_ID, _job(None)) is None


def test_runtime_block_errors_are_readable(job_db, material, storage, tmp_path: Path) -> None:
    with pytest.raises(MaterializeError, match="missing material_id"):
        material_runtime_block(
            job_db.path, tmp_path, WORKSPACE_ID, _job({"type": "material"}), storage=storage
        )
    with pytest.raises(MaterializeError, match="material not found"):
        material_runtime_block(
            job_db.path,
            tmp_path,
            WORKSPACE_ID,
            _job({"type": "material", "material_id": "nope"}),
            storage=storage,
        )
    with job_db.connect() as conn:
        conn.execute("update materials set status='uploading' where id=%s", (material,))
    with pytest.raises(MaterializeError, match="not ready"):
        material_runtime_block(
            job_db.path,
            tmp_path,
            WORKSPACE_ID,
            _job({"type": "material", "material_id": material}),
            storage=storage,
        )


def test_runtime_block_without_configured_storage(
    job_db, material, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("server.app.services.material_cache.build_s3_storage", lambda: None)

    with pytest.raises(MaterializeError, match="storage is not configured"):
        material_runtime_block(
            job_db.path,
            tmp_path,
            WORKSPACE_ID,
            _job({"type": "material", "material_id": material}),
        )


def test_claim_block_carries_presigned_url_without_storage_key(job_db, material, storage) -> None:
    block = material_claim_block(
        job_db.path,
        WORKSPACE_ID,
        _job({"type": "material", "material_id": material}),
        storage=storage,
    )

    assert block is not None
    assert block["material_id"] == material
    assert block["content_hash"] == HASH
    assert block["size_bytes"] == len(PAYLOAD)
    assert block["download_url"].startswith("https://s3.test/download/")
    assert "storage_key" not in block
    assert storage.presigned_gets == [STORAGE_KEY]
    # 非 material 输入不下发材料块。
    assert (
        material_claim_block(
            job_db.path,
            WORKSPACE_ID,
            _job({"type": "ref", "external_id": "q-1"}),
            storage=storage,
        )
        is None
    )
