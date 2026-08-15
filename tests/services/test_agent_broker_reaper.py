"""Bundle-GC tests for AgentExecutionBroker.reap_terminal_bundles."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

from server.app.agent_broker import AgentExecutionBroker
from tests.postgres_support import TEST_DATABASE_URL


def _insert_request(
    job_db,
    *,
    execution_id: str,
    state: str,
    bundle_name: str,
    finished_at: datetime | None = None,
) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('test-workspace', 'Test', 'question_comprehension_info')"
            " on conflict(id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
            " values ('job-1', 'test-workspace', 'questions', 'question', 'job-1')"
            " on conflict(id) do nothing"
        )
        conn.execute(
            "insert into agent_execution_requests("
            " execution_id, workspace_id, job_id, workflow_key, node_key,"
            " agent_id, agent_definition_hash, node_concurrency_limit,"
            " state, queued_at, finished_at, manifest_json)"
            " values (%s, 'test-workspace', 'job-1', 'questions', 'review',"
            " 'generator-v1', 'sha256:whatever', 1, %s, current_timestamp, %s, %s)",
            (execution_id, state, finished_at, json.dumps({"bundle_name": bundle_name})),
        )


def test_reap_terminal_bundles_removes_done_bundles_and_stale_archives(job_db, tmp_path) -> None:
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    live_bundle = bundle_dir / "live.tar.gz"
    live_bundle.write_bytes(b"bundle")
    done_bundle = bundle_dir / "done.tar.gz"
    done_bundle.write_bytes(b"bundle")
    fresh_archive = bundle_dir / "fresh.result.tar.gz"
    fresh_archive.write_bytes(b"archive")
    stale_archive = bundle_dir / "stale.result.tar.gz"
    stale_archive.write_bytes(b"archive")
    old = datetime.now(UTC).timestamp() - 7200
    os.utime(stale_archive, (old, old))

    broker = AgentExecutionBroker(TEST_DATABASE_URL, bundle_dir=bundle_dir, data_dir=tmp_path)
    # The queued request's bundle must survive; the terminal one (finished_at
    # NULL, covered by the startup full scan) is reaped.
    _insert_request(job_db, execution_id="exec-live", state="queued", bundle_name="live.tar.gz")
    _insert_request(job_db, execution_id="exec-done", state="done", bundle_name="done.tar.gz")

    reaped = broker.reap_terminal_bundles()

    assert reaped == 2
    assert live_bundle.is_file()
    assert not done_bundle.exists()
    assert fresh_archive.is_file()
    assert not stale_archive.exists()


def test_reap_terminal_bundles_reaps_stale_result_staging_files(job_db, tmp_path) -> None:
    """Staging files (.result-*.tmp) leaked by a crashed spool are reaped by
    age alongside orphaned archives; fresh ones (possibly mid-upload) stay."""
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    stale = bundle_dir / ".result-stale.tmp"
    stale.write_bytes(b"partial")
    fresh = bundle_dir / ".result-fresh.tmp"
    fresh.write_bytes(b"partial")
    old = datetime.now(UTC).timestamp() - 7200
    os.utime(stale, (old, old))

    broker = AgentExecutionBroker(TEST_DATABASE_URL, bundle_dir=bundle_dir, data_dir=tmp_path)
    reaped = broker.reap_terminal_bundles()

    assert reaped == 1
    assert not stale.exists()
    assert fresh.is_file()


def test_reap_terminal_bundles_incremental_uses_done_and_cancelled_branches(
    job_db, tmp_path
) -> None:
    """After the startup full scan sets the watermark, incremental passes must
    still reap both 'done' and 'cancelled' bundles (one indexed branch each)."""
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    broker = AgentExecutionBroker(TEST_DATABASE_URL, bundle_dir=bundle_dir, data_dir=tmp_path)

    broker.reap_terminal_bundles()  # startup full scan: sets the watermark
    assert broker._reap_watermark is not None

    for state in ("done", "cancelled"):
        (bundle_dir / f"{state}.tar.gz").write_bytes(b"bundle")
        _insert_request(
            job_db,
            execution_id=f"exec-{state}",
            state=state,
            bundle_name=f"{state}.tar.gz",
            finished_at=datetime.now(UTC),
        )

    reaped = broker.reap_terminal_bundles()

    assert reaped == 2
    assert not (bundle_dir / "done.tar.gz").exists()
    assert not (bundle_dir / "cancelled.tar.gz").exists()


def test_reap_incremental_query_never_seq_scans(job_db, tmp_path) -> None:
    """Pin the performance property: the incremental query must stay index
    driven. On production-scale tables the old OR-shaped predicate degraded
    to a parallel seq scan of the whole table every sweeper pass."""
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    broker = AgentExecutionBroker(TEST_DATABASE_URL, bundle_dir=bundle_dir, data_dir=tmp_path)
    broker.reap_terminal_bundles()  # sets the watermark
    watermark = broker._reap_watermark
    assert watermark is not None

    query = (
        "select manifest_json from agent_execution_requests"
        " where state='done' and finished_at >= %s"
        " union all"
        " select manifest_json from agent_execution_requests"
        " where state='cancelled' and finished_at >= %s"
    )
    with job_db.connect() as conn:
        rows = conn.execute(f"explain {query}", (watermark, watermark)).fetchall()

    plan = "\n".join(str(row[0]) for row in rows)
    assert "Seq Scan on agent_execution_requests" not in plan
