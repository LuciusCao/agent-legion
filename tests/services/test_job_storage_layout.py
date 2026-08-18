"""Sharded job storage layout: deterministic shard mapping and dual-path probing."""

from __future__ import annotations

from server.app.jobs.storage_layout import (
    job_shard,
    job_storage_dir,
    resolve_job_dir_candidates,
)
from tests.helpers.job_dirs import job_storage_ref, make_job_dir


def test_job_shard_is_two_hex_chars() -> None:
    shard = job_shard("demo_workspace_demo_workflow_0001e4bac62ce53715084637a2a66110")
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
    from server.app.services.job_run_dir_probe import derive_run_dir_from_log_path

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
    from server.app.services.job_run_dir_probe import derive_run_dir_from_log_path

    data_dir = tmp_path / "data"
    jobs_dir = data_dir / "jobs"
    job_id = "ws_wf_job-legacy"
    run_dir = make_job_dir(data_dir, "ws", job_id, sharded=False) / "runs" / "node-a" / "tok-1"
    run_dir.mkdir(parents=True)

    found = derive_run_dir_from_log_path(
        f"logs/jobs/{job_id}-node-a.log", "node-a", job_id, jobs_dir
    )
    assert found == run_dir


def test_derive_run_dir_from_log_path_probes_past_empty_sharded_dir(tmp_path) -> None:
    """Re-intake can leave an empty sharded dir while runs live in the legacy one."""
    from server.app.services.job_run_dir_probe import derive_run_dir_from_log_path

    data_dir = tmp_path / "data"
    jobs_dir = data_dir / "jobs"
    job_id = "ws_wf_job-both"
    make_job_dir(data_dir, "ws", job_id)  # empty sharded dir, no runs/
    legacy_run = make_job_dir(data_dir, "ws", job_id, sharded=False) / "runs" / "node-a" / "tok-1"
    legacy_run.mkdir(parents=True)

    found = derive_run_dir_from_log_path(
        f"logs/jobs/{job_id}-node-a.log", "node-a", job_id, jobs_dir
    )
    assert found == legacy_run


def test_locate_job_dir_rejects_paths_outside_jobs_root(tmp_path) -> None:
    from server.app.services.job_dir_index import locate_job_dir

    data_dir = tmp_path / "data"
    (data_dir / "jobs").mkdir(parents=True)
    (data_dir / "packages").mkdir(parents=True)

    assert locate_job_dir("job-1", "packages/workspace-ws/pkg.zip", data_dir) is None
    assert locate_job_dir("job-1", "videos/vid1", data_dir) is None
    assert locate_job_dir("job-1", "jobs/ws/ab/job-1", data_dir) is not None


def _seed_job(conn, job_id, workspace_id, storage_dir) -> None:
    conn.execute(
        "insert into workspaces(id, name, default_workflow_key) values (%s, %s, 'demo_workflow') on conflict (id) do nothing",
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


_REVISION = {"id": "rev-1", "version": 1, "definition_hash": "h", "definition_json": "{}"}


def _bulk_candidate(source_id: str) -> dict:
    return {"entity_id": source_id, "entity_type": "question", "title": "t"}


def test_create_jobs_bulk_creates_shard_dir_for_new_jobs(job_db, settings) -> None:
    data_dir = settings.data_dir
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('ws', 'ws', 'demo_workflow') on conflict (id) do nothing"
        )

    jobs = job_db.create_jobs_bulk(
        candidates=[_bulk_candidate("q-new")],
        workflow_key="wf",
        batch_id="batch-1",
        node_keys=["node-a"],
        workspace_id="ws",
        revision=_REVISION,
    )

    job_id = "ws_wf_q-new"
    assert [str(job["id"]) for job in jobs] == [job_id]
    assert job_storage_dir(data_dir / "jobs", "ws", job_id).is_dir()
    with job_db._connect_read() as conn:
        row = conn.execute("select storage_dir from jobs where id = %s", (job_id,)).fetchone()
    assert row["storage_dir"] == job_storage_ref("ws", job_id)


def test_create_jobs_bulk_resubmit_does_not_precreate_shard_dir(job_db, settings) -> None:
    """Resubmitting a legacy job keeps its stored storage_dir; a stray empty
    shard dir would block the flat→sharded migration as a conflict."""
    data_dir = settings.data_dir
    job_id = "ws_wf_q-legacy"  # _job_id("ws", "wf", "q-legacy")
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('ws', 'ws', 'demo_workflow') on conflict (id) do nothing"
        )
        conn.execute(
            """
            insert into jobs(id, workspace_id, workflow_key, source_type, source_id, storage_dir)
            values (%s, 'ws', 'wf', 'question', 'q-legacy', %s)
            """,
            (job_id, job_storage_ref("ws", job_id, sharded=False)),
        )

    jobs = job_db.create_jobs_bulk(
        candidates=[_bulk_candidate("q-legacy")],
        workflow_key="wf",
        batch_id="batch-2",
        node_keys=["node-a"],
        workspace_id="ws",
        revision=_REVISION,
    )

    assert [str(job["id"]) for job in jobs] == [job_id]
    assert not job_storage_dir(data_dir / "jobs", "ws", job_id).exists()
    with job_db._connect_read() as conn:
        row = conn.execute("select storage_dir from jobs where id = %s", (job_id,)).fetchone()
    assert row["storage_dir"] == job_storage_ref("ws", job_id, sharded=False)


def test_job_run_dir_candidates_prefers_authoritative_storage_dir(tmp_path) -> None:
    from server.app.services.job_run_dir_probe import job_run_dir_candidates

    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    job_id = "ws_wf_cand-storage"
    candidates = job_run_dir_candidates(jobs_dir, "ws", job_storage_ref("ws", job_id), job_id)
    # storage_dir resolves to the sharded path; the legacy flat probe follows,
    # and the duplicate sharded probe is deduped.
    assert candidates == (job_storage_dir(jobs_dir, "ws", job_id), jobs_dir / "ws" / job_id)


def test_job_run_dir_candidates_without_storage_dir(tmp_path) -> None:
    from server.app.services.job_run_dir_probe import job_run_dir_candidates

    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    job_id = "ws_wf_cand-plain"
    candidates = job_run_dir_candidates(jobs_dir, "ws", "", job_id)
    assert candidates == resolve_job_dir_candidates(jobs_dir, "ws", job_id)


def test_job_run_dir_candidates_rejects_escaping_storage_dir(tmp_path) -> None:
    from server.app.services.job_run_dir_probe import job_run_dir_candidates

    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    job_id = "ws_wf_cand-evil"
    candidates = job_run_dir_candidates(jobs_dir, "ws", "../outside", job_id)
    assert candidates == resolve_job_dir_candidates(jobs_dir, "ws", job_id)


def test_job_run_dir_candidates_missing_jobs_dir(tmp_path) -> None:
    from server.app.services.job_run_dir_probe import job_run_dir_candidates

    jobs_dir = tmp_path / "jobs"  # not created
    assert job_run_dir_candidates(jobs_dir, "ws", "", "ws_wf_cand-missing") == ()


def test_derive_run_dir_from_job_dirs_picks_newest_token(tmp_path) -> None:
    import os

    from server.app.services.job_run_dir_probe import derive_run_dir_from_job_dirs

    job_dir = tmp_path / "job"
    old_token = job_dir / "runs" / "node-a" / "tok-old"
    new_token = job_dir / "runs" / "node-a" / "tok-new"
    old_token.mkdir(parents=True)
    new_token.mkdir(parents=True)
    os.utime(old_token, (1_000_000, 1_000_000))
    os.utime(new_token, (2_000_000, 2_000_000))

    assert derive_run_dir_from_job_dirs([job_dir], "node-a") == new_token


def test_derive_run_dir_from_job_dirs_probes_past_empty_candidate(tmp_path) -> None:
    """An empty sharded dir must not hide run tokens in the legacy flat one."""
    from server.app.services.job_run_dir_probe import derive_run_dir_from_job_dirs

    data_dir = tmp_path / "data"
    job_id = "ws_wf_derive-both"
    sharded_dir = make_job_dir(data_dir, "ws", job_id)  # no runs/
    legacy_dir = make_job_dir(data_dir, "ws", job_id, sharded=False)
    legacy_run = legacy_dir / "runs" / "node-a" / "tok-1"
    legacy_run.mkdir(parents=True)

    assert derive_run_dir_from_job_dirs([sharded_dir, legacy_dir], "node-a") == legacy_run


def test_derive_run_dir_from_job_dirs_empty_inputs(tmp_path) -> None:
    from server.app.services.job_run_dir_probe import derive_run_dir_from_job_dirs

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    assert derive_run_dir_from_job_dirs([], "node-a") is None
    assert derive_run_dir_from_job_dirs([job_dir], "") is None
    assert derive_run_dir_from_job_dirs([job_dir], "node-a") is None
