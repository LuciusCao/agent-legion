from __future__ import annotations


def test_object_storage_key_queries_include_materials_on_gc_prefixes(job_db) -> None:
    artifact_key = "jobs/ws/job-1/out.json"
    shared_key = "jobs/shared.json"
    material_key = "jobs-staging/hash/doc.txt"
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values ('jobs', 'jobs'),"
            " ('jobs-staging', 'jobs-staging')"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id)"
            " values ('artifact-key-job', 'jobs', 'test', 'artifact-key-job')"
        )
        conn.execute(
            "insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes)"
            " values ('artifact-key-job', 'node', 'out.json', %s, 1),"
            " ('artifact-key-job', 'node', 'shared.json', %s, 1)",
            (artifact_key, shared_key),
        )
        conn.execute(
            "insert into materials(id, workspace_id, content_hash, storage_key, status)"
            " values ('material-shared', 'jobs', 'shared', %s, 'ready'),"
            " ('material-prefix', 'jobs-staging', 'prefix', %s, 'ready')",
            (shared_key, material_key),
        )

    assert job_db.all_object_storage_keys() == {artifact_key, shared_key, material_key}
    assert job_db.existing_object_storage_keys(
        [artifact_key, shared_key, material_key, "jobs/missing.json"]
    ) == {artifact_key, shared_key, material_key}
    assert job_db.existing_object_storage_keys([]) == set()
