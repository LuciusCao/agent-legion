"""Sharded job storage layout: deterministic shard mapping and dual-path probing."""

from __future__ import annotations

from server.app.jobs.storage_layout import (
    job_shard,
    job_storage_dir,
    resolve_job_dir_candidates,
)
from tests.helpers.job_dirs import job_storage_ref, make_job_dir


def test_job_shard_is_two_hex_chars() -> None:
    shard = job_shard(
        "question_comprehension_question_comprehension_info_0001e4bac62ce53715084637a2a66110"
    )
    assert len(shard) == 2
    assert all(c in "0123456789abcdef" for c in shard)


def test_job_shard_is_deterministic() -> None:
    job_id = "ws_wf_source-1"
    assert job_shard(job_id) == job_shard(job_id)


def test_job_shard_handles_non_hex_and_short_ids() -> None:
    assert len(job_shard("")) == 2
    assert len(job_shard("x")) == 2
    assert len(job_shard("视频_x50010107")) == 2


def test_job_shard_spreads_across_buckets() -> None:
    shards = {job_shard(f"ws_wf_{i:032x}") for i in range(2000)}
    assert len(shards) > 200


def test_job_storage_dir_layout(tmp_path) -> None:
    jobs_dir = tmp_path / "jobs"
    job_id = "ws_wf_abc123"
    path = job_storage_dir(jobs_dir, "ws", job_id)
    assert path == jobs_dir / "ws" / job_shard(job_id) / job_id


def test_resolve_job_dir_candidates_sharded_first(tmp_path) -> None:
    jobs_dir = tmp_path / "jobs"
    job_id = "ws_wf_abc123"
    sharded, legacy = resolve_job_dir_candidates(jobs_dir, "ws", job_id)
    assert sharded == job_storage_dir(jobs_dir, "ws", job_id)
    assert legacy == jobs_dir / "ws" / job_id


def test_derive_run_dir_from_log_path_finds_sharded_layout(tmp_path) -> None:
    from server.app.storage_paths import derive_run_dir_from_log_path

    data_dir = tmp_path / "data"
    jobs_dir = data_dir / "jobs"
    job_id = "ws_wf_job-sharded"
    run_dir = make_job_dir(data_dir, "ws", job_id) / "runs" / "node-a" / "tok-1"
    run_dir.mkdir(parents=True)

    found = derive_run_dir_from_log_path(
        f"logs/jobs/{job_id}-node-a.log", "node-a", job_id, jobs_dir
    )
    assert found == run_dir


def test_derive_run_dir_from_log_path_finds_legacy_layout(tmp_path) -> None:
    from server.app.storage_paths import derive_run_dir_from_log_path

    data_dir = tmp_path / "data"
    jobs_dir = data_dir / "jobs"
    job_id = "ws_wf_job-legacy"
    run_dir = make_job_dir(data_dir, "ws", job_id, sharded=False) / "runs" / "node-a" / "tok-1"
    run_dir.mkdir(parents=True)

    found = derive_run_dir_from_log_path(
        f"logs/jobs/{job_id}-node-a.log", "node-a", job_id, jobs_dir
    )
    assert found == run_dir


def _seed_job(conn, job_id, workspace_id, storage_dir) -> None:
    conn.execute(
        "insert into workspaces(id, name) values (%s, %s) on conflict (id) do nothing",
        (workspace_id, workspace_id),
    )
    conn.execute(
        """
        insert into jobs(id, workspace_id, workflow_key, source_type, source_id, storage_dir)
        values (%s, %s, 'wf', 'question', %s, %s)
        """,
        (job_id, workspace_id, job_id, storage_dir),
    )


def test_build_job_dir_index_resolves_both_layouts(job_db, settings) -> None:
    from server.app.services.job_dir_index import build_job_dir_index

    data_dir = settings.data_dir
    with job_db.connect() as conn:
        _seed_job(conn, "job-sharded", "ws", job_storage_ref("ws", "job-sharded"))
        _seed_job(conn, "job-legacy", "ws", job_storage_ref("ws", "job-legacy", sharded=False))
        _seed_job(conn, "job-no-storage", "ws", "")
        _seed_job(conn, "job-bad-path", "ws", "../escape")

    index = build_job_dir_index(
        job_db, data_dir, {"job-sharded", "job-legacy", "job-no-storage", "job-bad-path", "missing"}
    )

    assert index["job-sharded"] == data_dir / job_storage_ref("ws", "job-sharded")
    assert index["job-legacy"] == data_dir / "jobs" / "ws" / "job-legacy"
    assert index["job-no-storage"] == data_dir / "jobs" / "job-no-storage"
    assert "job-bad-path" not in index
    assert "missing" not in index


def test_cleanup_extra_runs_per_node_sharded_layout(job_db, settings) -> None:
    from server.app.services.cleanup_sweep import cleanup_extra_runs_per_node

    data_dir = settings.data_dir
    job_id = "ws_wf_job-cleanup"
    node_dir = make_job_dir(data_dir, "ws", job_id) / "runs" / "node-a"
    old_dir = node_dir / "tok-old"
    new_dir = node_dir / "tok-new"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    old_ts = 1000000.0
    new_ts = 2000000.0
    import os

    os.utime(old_dir, (old_ts, old_ts))
    os.utime(new_dir, (new_ts, new_ts))

    with job_db.connect() as conn:
        _seed_job(conn, job_id, "ws", job_storage_ref("ws", job_id))
        conn.execute(
            "insert into node_runs(job_id, node_key, status, run_dir) values (%s, 'node-a', 'completed', %s)",
            (job_id, f"{job_storage_ref('ws', job_id)}/runs/node-a/tok-old"),
        )

    removed = cleanup_extra_runs_per_node(job_db, data_dir)

    assert removed == 1
    assert not old_dir.exists()
    assert new_dir.exists()
