"""Shared fixtures and helpers for the materials service test siblings.

The materials service tests split by theme (presign/delete in
test_materials.py, complete() in test_materials_complete.py); fixtures and
row-level helpers live here so both files bind the same names
(``storage = materials_testlib.storage``) and test bodies stay identical.
"""

from __future__ import annotations

import hashlib

import pytest

from server.app.services.materials import MaterialsService
from tests.fakes.storage import FakeObjectStorage

WORKSPACE_ID = "ws-materials"
OTHER_WORKSPACE_ID = "ws-materials-other"


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture
def storage() -> FakeObjectStorage:
    return FakeObjectStorage()


@pytest.fixture
def service(job_db, storage) -> MaterialsService:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, 'Materials') on conflict(id) do nothing",
            (WORKSPACE_ID,),
        )
    return MaterialsService(job_db.dsn_identity, storage)


def presign_ready(service: MaterialsService, storage: FakeObjectStorage, payload: bytes) -> dict:
    """Run the full presign → PUT → complete flow against the fake storage."""
    content_hash = sha256_hex(payload)
    result = service.presign(
        WORKSPACE_ID,
        filename="notes.txt",
        size_bytes=len(payload),
        content_type="text/plain",
        content_hash=content_hash,
        created_by="user-1",
    )
    material_id = result["material"]["id"]
    storage.objects[f"{WORKSPACE_ID}/{content_hash}/v2g0/m_{material_id}/notes.txt"] = payload
    return service.complete(WORKSPACE_ID, material_id)


def insert_material_row(
    job_db,
    *,
    material_id: str,
    content_hash: str,
    filename: str,
    storage_key: str,
    size_bytes: int = 1,
) -> None:
    """直接落一条材料行，模拟校验收紧（#1119/#1267）前入库的 legacy 行。"""
    with job_db.connect() as conn:
        conn.execute(
            "insert into materials(id, workspace_id, content_hash, filename, content_type,"
            " size_bytes, storage_key, status, created_by)"
            " values (%s, %s, %s, %s, 'text/plain', %s, %s, 'failed', 'user-1')",
            (material_id, WORKSPACE_ID, content_hash, filename, size_bytes, storage_key),
        )


def storage_key_of(job_db, material_id: str) -> str:
    with job_db.connect() as conn:
        row = conn.execute(
            "select storage_key from materials where id=%s", (material_id,)
        ).fetchone()
    assert row is not None
    return str(row["storage_key"])


def generation_of(job_db, material_id: str) -> int:
    with job_db.connect() as conn:
        row = conn.execute(
            "select upload_generation from materials where id=%s", (material_id,)
        ).fetchone()
    assert row is not None
    return int(row["upload_generation"])


def object_etag_of(job_db, material_id: str) -> str:
    with job_db.connect() as conn:
        row = conn.execute(
            "select object_etag from materials where id=%s", (material_id,)
        ).fetchone()
    assert row is not None
    return str(row["object_etag"])
