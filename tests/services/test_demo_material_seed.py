"""Demo material seed (design §9): seed-if-absent, graceful S3 degradation."""

from __future__ import annotations

import hashlib
import io
import logging
from pathlib import Path

import pytest

from server.app.services.demo_material_seed import (
    DEMO_MATERIALS_DIR,
    seed_demo_workspace_materials,
)
from server.app.storage import ObjectHead


class FakeStorage:
    """In-memory ObjectStorage test double with a put_object switch."""

    def __init__(self, *, fail_put: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_put = fail_put

    def presign_put(self, storage_key: str, size_bytes: int, expires_seconds: int = 3600) -> str:
        return f"https://s3.test/upload/{storage_key}"

    def presign_get(self, storage_key: str, expires_seconds: int = 3600) -> str:
        return f"https://s3.test/download/{storage_key}"

    def head_object(self, storage_key: str) -> ObjectHead | None:
        payload = self.objects.get(storage_key)
        return None if payload is None else ObjectHead(size_bytes=len(payload))

    def open_stream(self, storage_key: str) -> io.BytesIO:
        return io.BytesIO(self.objects[storage_key])

    def put_object(self, storage_key: str, data: bytes, content_type: str = "") -> None:
        if self.fail_put:
            raise ConnectionError("endpoint unreachable")
        self.objects[storage_key] = data

    def delete_object(self, storage_key: str) -> None:
        self.objects.pop(storage_key, None)


def _seeded_rows(job_db, workspace_id: str) -> list[dict]:
    with job_db.connect() as conn:
        rows = conn.execute(
            "select * from materials where workspace_id=%s order by filename",
            (workspace_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def test_seed_uploads_examples_as_ready_materials(job_db, settings) -> None:
    workspace_id = str(
        job_db.create_workspace("demo", default_workflow_key="education_video_problems_generation")[
            "id"
        ]
    )
    storage = FakeStorage()

    seeded = seed_demo_workspace_materials(settings, workspace_id, storage=storage)

    examples = sorted(path.name for path in (settings.root_dir / DEMO_MATERIALS_DIR).glob("*.md"))
    assert seeded == examples
    rows = _seeded_rows(job_db, workspace_id)
    assert [row["filename"] for row in rows] == examples
    for row in rows:
        assert row["status"] == "ready"
        assert row["created_by"] == "system"
        payload = (settings.root_dir / DEMO_MATERIALS_DIR / row["filename"]).read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        assert row["content_hash"] == digest
        assert row["size_bytes"] == len(payload)
        assert row["storage_key"] == f"{workspace_id}/{digest}/{row['filename']}"
        assert storage.objects[row["storage_key"]] == payload


def test_seed_is_idempotent(job_db, settings) -> None:
    workspace_id = str(
        job_db.create_workspace("demo", default_workflow_key="education_video_problems_generation")[
            "id"
        ]
    )
    storage = FakeStorage()

    first = seed_demo_workspace_materials(settings, workspace_id, storage=storage)
    assert first
    assert seed_demo_workspace_materials(settings, workspace_id, storage=storage) == []
    assert len(_seeded_rows(job_db, workspace_id)) == len(first)


def test_seed_skips_when_storage_unconfigured(
    job_db, settings, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_id = str(
        job_db.create_workspace("demo", default_workflow_key="education_video_problems_generation")[
            "id"
        ]
    )
    monkeypatch.setattr("server.app.services.demo_material_seed.build_s3_storage", lambda: None)

    with caplog.at_level(logging.WARNING):
        assert seed_demo_workspace_materials(settings, workspace_id) == []

    assert "AGENT_LEGION_S3_BUCKET" in caplog.text
    assert _seeded_rows(job_db, workspace_id) == []


def test_seed_skips_when_examples_tree_missing(
    job_db, settings, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_id = str(
        job_db.create_workspace("demo", default_workflow_key="education_video_problems_generation")[
            "id"
        ]
    )
    settings.root_dir = tmp_path  # SPA-style root without the examples tree

    with caplog.at_level(logging.WARNING):
        assert seed_demo_workspace_materials(settings, workspace_id, storage=FakeStorage()) == []

    assert "not found" in caplog.text
    assert _seeded_rows(job_db, workspace_id) == []


def test_seed_aborts_gracefully_when_store_unreachable(job_db, settings) -> None:
    workspace_id = str(
        job_db.create_workspace("demo", default_workflow_key="education_video_problems_generation")[
            "id"
        ]
    )

    seeded = seed_demo_workspace_materials(
        settings, workspace_id, storage=FakeStorage(fail_put=True)
    )

    assert seeded == []
    assert _seeded_rows(job_db, workspace_id) == []
