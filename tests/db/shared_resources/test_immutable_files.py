from __future__ import annotations

import psycopg
import pytest

_READY = "version-ready"
_COPYING = "version-copying"
_FAILED = "version-failed"
_MANIFEST = "a" * 64


def _seed_versions(job_db) -> None:
    with job_db.write() as conn:
        conn.execute("insert into workspaces(id, name) values ('immutable-ws', 'Immutable')")
        conn.execute(
            "insert into shared_resources(id, owner_workspace_id, name, create_token,"
            " create_request_hash, created_by) values"
            " ('resource-immutable', 'immutable-ws', 'Immutable', 'create', 'request', 'actor')"
        )
        for version_id, version, status, manifest in (
            (_READY, 1, "copying", _MANIFEST),
            (_COPYING, 2, "copying", "b" * 64),
            (_FAILED, 3, "failed", "c" * 64),
        ):
            conn.execute(
                "insert into shared_resource_versions(id, resource_id, version, status,"
                " source_type, source_id, source_workspace_id, source_snapshot_json,"
                " manifest_hash, idempotency_key, request_hash, created_by, ready_at)"
                " values (%s, 'resource-immutable', %s, %s, 'material', 'source-material',"
                " 'immutable-ws', '{\"files\": []}', %s, %s, 'request', 'actor',"
                " case when %s = 'ready' then current_timestamp else null end)",
                (version_id, version, status, manifest, f"token-{version}", status),
            )
        for version_id, status in ((_READY, "ready"), (_COPYING, "copying"), (_FAILED, "failed")):
            conn.execute(
                "insert into shared_resource_files(id, version_id, ordinal, path, sha256,"
                " size_bytes, storage_key, source_storage_key, source_material_id,"
                " source_upload_generation, source_etag) values"
                " (%s, %s, 0, %s, %s, 1, %s, 'source/key', 'source-material', 0, 'etag')",
                (
                    f"{status}-file",
                    version_id,
                    f"{status}.txt",
                    "d" * 64,
                    f"shared-resources/resource-immutable/{version_id}/g1/{status}-file",
                ),
            )
        conn.execute(
            "update shared_resource_versions set status='ready', ready_at=current_timestamp"
            " where id=%s",
            (_READY,),
        )


def _files(job_db, version_id: str) -> list[dict[str, object]]:
    with job_db.read() as conn:
        rows = conn.execute(
            "select id, version_id, ordinal, path, storage_key, sha256, size_bytes"
            " from shared_resource_files where version_id=%s order by id",
            (version_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _ready_manifest(job_db) -> str:
    with job_db.read() as conn:
        row = conn.execute(
            "select manifest_hash from shared_resource_versions where id=%s", (_READY,)
        ).fetchone()
    return str(row["manifest_hash"])


@pytest.mark.parametrize("source_status", ["copying", "failed"])
def test_nonready_file_cannot_move_into_ready_version(job_db, source_status: str) -> None:
    _seed_versions(job_db)
    source_version = _COPYING if source_status == "copying" else _FAILED
    before_ready = _files(job_db, _READY)
    before_source = _files(job_db, source_version)

    with pytest.raises(psycopg.DatabaseError, match="immutable"), job_db.write() as conn:
        conn.execute(
            "update shared_resource_files set version_id=%s, path=%s, ordinal=90,"
            " storage_key=%s where id=%s",
            (
                _READY,
                f"unverified-{source_status}.txt",
                f"shared-resources/unverified/{source_status}",
                f"{source_status}-file",
            ),
        )

    assert _files(job_db, _READY) == before_ready
    assert _files(job_db, source_version) == before_source
    assert _ready_manifest(job_db) == _MANIFEST


def test_insert_into_ready_version_is_rejected(job_db) -> None:
    _seed_versions(job_db)
    before_ready = _files(job_db, _READY)

    with pytest.raises(psycopg.DatabaseError, match="immutable"), job_db.write() as conn:
        conn.execute(
            "insert into shared_resource_files(id, version_id, ordinal, path, sha256,"
            " size_bytes, storage_key, source_storage_key, source_material_id,"
            " source_upload_generation, source_etag) values"
            " ('injected-file', %s, 91, 'injected.txt', %s, 1,"
            " 'shared-resources/unverified/insert', 'source/key', 'source-material', 0, 'etag')",
            (_READY, "e" * 64),
        )

    assert _files(job_db, _READY) == before_ready
    assert _ready_manifest(job_db) == _MANIFEST


def test_ready_file_cannot_move_to_nonready_version_or_be_deleted(job_db) -> None:
    _seed_versions(job_db)
    before_ready = _files(job_db, _READY)
    before_copying = _files(job_db, _COPYING)

    with pytest.raises(psycopg.DatabaseError, match="immutable"), job_db.write() as conn:
        conn.execute(
            "update shared_resource_files set version_id=%s, path='outbound.txt',"
            " ordinal=92, storage_key='shared-resources/unverified/outbound' where id='ready-file'",
            (_COPYING,),
        )
    with pytest.raises(psycopg.DatabaseError, match="immutable"), job_db.write() as conn:
        conn.execute("delete from shared_resource_files where id='ready-file'")

    assert _files(job_db, _READY) == before_ready
    assert _files(job_db, _COPYING) == before_copying
    assert _ready_manifest(job_db) == _MANIFEST


def test_nonready_insert_move_update_and_delete_remain_legal(job_db) -> None:
    _seed_versions(job_db)

    with job_db.write() as conn:
        conn.execute(
            "insert into shared_resource_files(id, version_id, ordinal, path, sha256,"
            " size_bytes, storage_key, source_storage_key, source_material_id,"
            " source_upload_generation, source_etag) values"
            " ('extra-copying-file', %s, 1, 'extra.txt', %s, 1, 'nonready/extra',"
            " 'source/key', 'source-material', 0, 'etag')",
            (_COPYING, "f" * 64),
        )
        conn.execute(
            "update shared_resource_files set version_id=%s, path='moved-valid.txt',"
            " ordinal=7, storage_key='nonready/moved-valid' where id='copying-file'",
            (_FAILED,),
        )
        conn.execute("delete from shared_resource_files where id='extra-copying-file'")

    moved = [file for file in _files(job_db, _FAILED) if file["id"] == "copying-file"]
    assert len(moved) == 1
    assert moved[0]["path"] == "moved-valid.txt"
    assert moved[0]["ordinal"] == 7
    assert moved[0]["storage_key"] == "nonready/moved-valid"
    assert _files(job_db, _COPYING) == []
