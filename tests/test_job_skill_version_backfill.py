from __future__ import annotations

import json
from contextlib import closing

from server.app.db.connection import connect_database
from server.app.services.job_skill_version_backfill import backfill_node_run_skill_versions
from tests.postgres_support import TEST_DATABASE_URL


def _setup(conn) -> None:
    conn.execute(
        "insert into workspaces(id, name) values ('ws1', 'ws1') on conflict (id) do nothing"
    )


def test_backfill_reads_run_json_and_refreshes_manifest(tmp_path):
    data_dir = tmp_path / "data"
    job_dir = data_dir / "jobs" / "ws1" / "job-1"
    run_dir = job_dir / "runs" / "generate_key_info" / "tok-1"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({"skill_version": "v1.2.3@abc123"}),
        encoding="utf-8",
    )
    (job_dir / "manifest.json").write_text(
        json.dumps(
            {
                "skill_versions": {
                    "generate_key_info": "unavailable",
                    "clean_and_parse": "unavailable",
                }
            }
        ),
        encoding="utf-8",
    )

    with closing(connect_database(TEST_DATABASE_URL)) as conn:
        _setup(conn)
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id, storage_dir)"
            " values (?, 'ws1', 'wf', 'question', 'q1', ?)",
            ("job-1", "jobs/ws1/job-1"),
        )
        conn.execute(
            """
            insert into node_runs(job_id, node_key, status, run_dir, skill_version)
            values (?, ?, 'completed', ?, '')
            """,
            ("job-1", "generate_key_info", "jobs/ws1/job-1/runs/generate_key_info/tok-1"),
        )
        conn.commit()

        result = backfill_node_run_skill_versions(conn, data_dir)

        assert result.node_runs_updated == 1
        assert result.manifests_updated == 1
        row = conn.execute("select skill_version from node_runs").fetchone()
        assert row["skill_version"] == "v1.2.3@abc123"
        manifest = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["skill_versions"] == {"generate_key_info": "v1.2.3@abc123"}


def test_backfill_skips_missing_or_empty_run_json(tmp_path):
    data_dir = tmp_path / "data"
    run_dir = data_dir / "jobs" / "ws1" / "job-1" / "runs" / "generate_key_info" / "tok-1"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"skill_version": ""}), encoding="utf-8")

    with closing(connect_database(TEST_DATABASE_URL)) as conn:
        _setup(conn)
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id, storage_dir)"
            " values (?, 'ws1', 'wf', 'question', 'q1', ?)",
            ("job-1", "jobs/ws1/job-1"),
        )
        conn.execute(
            """
            insert into node_runs(job_id, node_key, status, run_dir, skill_version)
            values (?, ?, 'completed', ?, '')
            """,
            ("job-1", "generate_key_info", "jobs/ws1/job-1/runs/generate_key_info/tok-1"),
        )
        conn.commit()

        result = backfill_node_run_skill_versions(conn, data_dir)

        assert result.node_runs_updated == 0
        row = conn.execute("select skill_version from node_runs").fetchone()
        assert row["skill_version"] == ""


def test_backfill_refreshes_manifest_when_versions_already_persisted(tmp_path):
    data_dir = tmp_path / "data"
    job_dir = data_dir / "jobs" / "ws1" / "job-1"
    job_dir.mkdir(parents=True)
    (job_dir / "manifest.json").write_text(
        json.dumps(
            {
                "skill_versions": {
                    "generate_key_info": "unavailable",
                    "clean_and_parse": "unavailable",
                }
            }
        ),
        encoding="utf-8",
    )

    with closing(connect_database(TEST_DATABASE_URL)) as conn:
        _setup(conn)
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id, storage_dir)"
            " values (?, 'ws1', 'wf', 'question', 'q1', ?)",
            ("job-1", "jobs/ws1/job-1"),
        )
        conn.execute(
            """
            insert into node_runs(job_id, node_key, status, run_dir, skill_version)
            values (?, ?, 'completed', '', ?)
            """,
            ("job-1", "generate_key_info", "v1.2.3@abc123"),
        )
        conn.commit()

        result = backfill_node_run_skill_versions(conn, data_dir)

        assert result.node_runs_updated == 0
        assert result.manifests_updated == 1
        manifest = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["skill_versions"] == {"generate_key_info": "v1.2.3@abc123"}
