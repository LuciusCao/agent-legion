"""Tests for scripts.migrate_job_dirs_to_shards against a real DB and filesystem."""

from __future__ import annotations

from scripts import migrate_job_dirs_to_shards as mig
from server.app.jobs.storage_layout import job_storage_dir
from tests.helpers.job_dirs import job_storage_ref


def _seed_job(conn, job_id, workspace_id, storage_dir):
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


def _storage_dir(db, job_id) -> str:
    with db._connect_read() as conn:
        row = conn.execute("select storage_dir from jobs where id = %s", (job_id,)).fetchone()
    return row["storage_dir"]


def test_migrates_legacy_flat_dir(job_db, settings):
    data_dir = settings.data_dir
    old_dir = data_dir / "jobs" / "ws1" / "job-1"
    old_dir.mkdir(parents=True)
    (old_dir / "result.json").write_text("{}")
    with job_db.connect() as conn:
        _seed_job(conn, "job-1", "ws1", job_storage_ref("ws1", "job-1", sharded=False))

    stats = mig.migrate_job_dirs(job_db, data_dir, apply=True)

    new_dir = job_storage_dir(data_dir / "jobs", "ws1", "job-1")
    assert stats.migrated == 1
    assert not old_dir.exists()
    assert (new_dir / "result.json").is_file()
    assert _storage_dir(job_db, "job-1") == job_storage_ref("ws1", "job-1")


def test_skips_already_sharded_rows(job_db, settings):
    data_dir = settings.data_dir
    sharded = job_storage_dir(data_dir / "jobs", "ws1", "job-new")
    sharded.mkdir(parents=True)
    with job_db.connect() as conn:
        _seed_job(conn, "job-new", "ws1", job_storage_ref("ws1", "job-new"))

    stats = mig.migrate_job_dirs(job_db, data_dir, apply=True)

    assert stats.migrated == 0
    assert stats.already_sharded == 1
    assert _storage_dir(job_db, "job-new") == job_storage_ref("ws1", "job-new")


def test_heals_db_when_rename_landed_but_update_missed(job_db, settings):
    data_dir = settings.data_dir
    # Simulate an interrupted run: dir already at the sharded path, DB still legacy.
    new_dir = job_storage_dir(data_dir / "jobs", "ws1", "job-2")
    new_dir.mkdir(parents=True)
    with job_db.connect() as conn:
        _seed_job(conn, "job-2", "ws1", job_storage_ref("ws1", "job-2", sharded=False))

    stats = mig.migrate_job_dirs(job_db, data_dir, apply=True)

    assert stats.migrated == 0
    assert stats.healed_db_only == 1
    assert _storage_dir(job_db, "job-2") == job_storage_ref("ws1", "job-2")


def test_conflict_is_reported_and_skipped(job_db, settings):
    data_dir = settings.data_dir
    old_dir = data_dir / "jobs" / "ws1" / "job-3"
    old_dir.mkdir(parents=True)
    new_dir = job_storage_dir(data_dir / "jobs", "ws1", "job-3")
    new_dir.mkdir(parents=True)
    with job_db.connect() as conn:
        _seed_job(conn, "job-3", "ws1", job_storage_ref("ws1", "job-3", sharded=False))

    stats = mig.migrate_job_dirs(job_db, data_dir, apply=True)

    assert stats.migrated == 0
    assert stats.conflicts == ["job-3"]
    assert old_dir.is_dir() and new_dir.is_dir()
    assert _storage_dir(job_db, "job-3") == job_storage_ref("ws1", "job-3", sharded=False)


def test_missing_dir_is_counted_and_untouched(job_db, settings):
    with job_db.connect() as conn:
        _seed_job(conn, "job-4", "ws1", job_storage_ref("ws1", "job-4", sharded=False))

    stats = mig.migrate_job_dirs(job_db, settings.data_dir, apply=True)

    assert stats.migrated == 0
    assert stats.missing_dir == 1
    assert _storage_dir(job_db, "job-4") == job_storage_ref("ws1", "job-4", sharded=False)


def test_dry_run_changes_nothing(job_db, settings):
    data_dir = settings.data_dir
    old_dir = data_dir / "jobs" / "ws1" / "job-5"
    old_dir.mkdir(parents=True)
    with job_db.connect() as conn:
        _seed_job(conn, "job-5", "ws1", job_storage_ref("ws1", "job-5", sharded=False))

    stats = mig.migrate_job_dirs(job_db, data_dir, apply=False)

    assert stats.migrated == 1
    assert old_dir.is_dir()
    assert _storage_dir(job_db, "job-5") == job_storage_ref("ws1", "job-5", sharded=False)


def test_paginates_batches(job_db, settings, monkeypatch):
    data_dir = settings.data_dir
    monkeypatch.setattr(mig, "_BATCH_SIZE", 2)
    with job_db.connect() as conn:
        for i in range(3):
            job_id = f"job-p{i}"
            (data_dir / "jobs" / "ws1" / job_id).mkdir(parents=True)
            _seed_job(conn, job_id, "ws1", job_storage_ref("ws1", job_id, sharded=False))

    stats = mig.migrate_job_dirs(job_db, data_dir, apply=True)

    assert stats.migrated == 3
    for i in range(3):
        assert _storage_dir(job_db, f"job-p{i}") == job_storage_ref("ws1", f"job-p{i}")
