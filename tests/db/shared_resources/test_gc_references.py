from __future__ import annotations

from server.app.jobs.queries.artifact_key_gc import (
    load_all_object_storage_keys,
    load_existing_object_storage_keys,
)


def test_shared_resource_file_keys_join_the_global_gc_reference_set(job_db) -> None:
    key = "shared-resources/resource-1/version-1/g1/file-1"
    with job_db.write() as conn:
        conn.execute("insert into workspaces(id, name) values ('gc-source', 'GC source')")
        conn.execute(
            "insert into shared_resources(id, owner_workspace_id, name, create_token,"
            " create_request_hash, created_by) values"
            " ('resource-1', 'gc-source', 'GC', 'token', 'request', 'actor')"
        )
        conn.execute(
            "insert into shared_resource_versions(id, resource_id, version, status, source_type,"
            " source_id, source_workspace_id, source_snapshot_json, manifest_hash,"
            " idempotency_key, request_hash, created_by) values"
            " ('version-1', 'resource-1', 1, 'copying', 'material', 'material-1', 'gc-source',"
            " '{}', 'manifest', 'token', 'request', 'actor')"
        )
        conn.execute(
            "insert into shared_resource_files(id, version_id, ordinal, path, sha256, size_bytes,"
            " storage_key, source_storage_key, source_material_id, source_upload_generation,"
            " source_etag) values"
            " ('file-1', 'version-1', 0, 'a.txt', %s, 1, %s, 'source/key', 'material-1', 0, 'etag')",
            ("0" * 64, key),
        )

    with job_db.read() as conn:
        assert key in load_all_object_storage_keys(conn)
        assert load_existing_object_storage_keys(conn, [key, "missing/key"]) == {key}
