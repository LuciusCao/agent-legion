"""Bundle-GC tests for AgentExecutionBroker.reap_terminal_bundles."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from server.app.agent_broker import AgentExecutionBroker, reaper
from server.app.db.connection import DatabaseConnection
from tests.postgres_support import TEST_DATABASE_URL


def _insert_request(
    job_db,
    *,
    execution_id: str,
    state: str,
    bundle_name: str,
    finished_at: datetime | None = None,
    manifest_text: str | None = None,
) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('test-workspace', 'Test', 'demo_workflow')"
            " on conflict(id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id)"
            " values ('job-1', 'test-workspace', 'question', 'job-1')"
            " on conflict(id) do nothing"
        )
        conn.execute(
            "insert into agent_execution_requests("
            " execution_id, workspace_id, job_id, node_key,"
            " agent_id, agent_definition_hash, node_concurrency_limit,"
            " state, queued_at, finished_at, manifest_json)"
            " values (%s, 'test-workspace', 'job-1', 'review',"
            " 'generator-v1', 'sha256:whatever', 1, %s, current_timestamp, %s, %s)",
            (
                execution_id,
                state,
                finished_at,
                manifest_text
                if manifest_text is not None
                else json.dumps({"bundle_name": bundle_name}),
            ),
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
    _insert_request(job_db, execution_id="live", state="queued", bundle_name="live.tar.gz")
    _insert_request(job_db, execution_id="done", state="done", bundle_name="done.tar.gz")

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
            execution_id=state,
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
        "select execution_id || '.tar.gz' as bundle_name from agent_execution_requests"
        " where state='done' and finished_at >= %s"
        " union all"
        " select execution_id || '.tar.gz' as bundle_name from agent_execution_requests"
        " where state='cancelled' and finished_at >= %s"
    )
    with job_db.connect() as conn:
        # enable_seqscan=off：见 test_agent_stock.py 同名钉扎测试的注释——
        # 小表上裸 EXPLAIN 会随统计/调度抖动，本测试钉的是索引可用性。
        conn.execute("set enable_seqscan=off")
        rows = conn.execute(f"explain {query}", (watermark, watermark)).fetchall()

    plan = "\n".join(str(row[0]) for row in rows)
    assert "Seq Scan on agent_execution_requests" not in plan


def test_startup_full_scan_streams_in_chunks(job_db, tmp_path, monkeypatch) -> None:
    """#128: the startup scan must stream through a chunked server-side cursor
    instead of buffering every terminal manifest at once. With a chunk size
    smaller than the row count (3 fetch batches for 5 rows), every bundle is
    still reaped and the watermark advances with the overlap anchored at the
    scan start."""
    monkeypatch.setattr(reaper, "_SCAN_CHUNK_SIZE", 2)
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    broker = AgentExecutionBroker(TEST_DATABASE_URL, bundle_dir=bundle_dir, data_dir=tmp_path)
    for index in range(5):
        (bundle_dir / f"done-{index}.tar.gz").write_bytes(b"bundle")
        _insert_request(
            job_db, execution_id=f"done-{index}", state="done", bundle_name=f"done-{index}.tar.gz"
        )

    with patch.object(
        DatabaseConnection, "stream", autospec=True, wraps=DatabaseConnection.stream
    ) as stream_spy:
        before = datetime.now(UTC) - reaper._REAP_OVERLAP
        reaped = broker.reap_terminal_bundles()
        after = datetime.now(UTC) - reaper._REAP_OVERLAP

    assert reaped == 5
    assert not list(bundle_dir.glob("done-*.tar.gz"))
    assert stream_spy.call_count == 1
    assert stream_spy.call_args.kwargs["chunk_size"] == 2
    query = stream_spy.call_args.args[2]
    assert "execution_id || '.tar.gz'" in query
    assert "manifest_json" not in query
    assert broker._reap_watermark is not None
    assert before <= broker._reap_watermark <= after


def test_startup_full_scan_bad_manifest_does_not_abort_later_chunks(
    job_db, tmp_path, monkeypatch
) -> None:
    """A manifest that fails JSON parsing is skipped without aborting the
    scan: with one row per fetch batch, the valid row in a later chunk is
    still reaped."""
    monkeypatch.setattr(reaper, "_SCAN_CHUNK_SIZE", 1)
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    broker = AgentExecutionBroker(TEST_DATABASE_URL, bundle_dir=bundle_dir, data_dir=tmp_path)
    # Inserted first so the poisoned row leads the scan; chunk size 1 puts it
    # in an earlier fetch batch than the valid row.
    _insert_request(
        job_db, execution_id="bad", state="done", bundle_name="", manifest_text="not json"
    )
    (bundle_dir / "good.tar.gz").write_bytes(b"bundle")
    _insert_request(job_db, execution_id="good", state="done", bundle_name="good.tar.gz")

    reaped = broker.reap_terminal_bundles()

    assert reaped == 1
    assert not (bundle_dir / "good.tar.gz").exists()


def test_startup_full_scan_ignores_json_nul_escape(job_db, tmp_path) -> None:
    """A legal JSON NUL escape elsewhere in the manifest must not abort GC."""
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    bundle = bundle_dir / "nul.tar.gz"
    bundle.write_bytes(b"bundle")
    _insert_request(
        job_db,
        execution_id="nul",
        state="done",
        bundle_name=bundle.name,
        manifest_text=json.dumps({"bundle_name": bundle.name, "prompt": "\x00"}),
    )
    broker = AgentExecutionBroker(TEST_DATABASE_URL, bundle_dir=bundle_dir, data_dir=tmp_path)

    assert broker.reap_terminal_bundles() == 1
    assert not bundle.exists()


def _persisted_watermark(job_db) -> datetime | None:
    with job_db.connect() as conn:
        row = conn.execute(
            "select value from global_settings where key='reap_watermark'"
        ).fetchone()
    if row is None:
        return None
    return datetime.fromisoformat(json.loads(str(row["value"]))["watermark"])


def test_reap_watermark_persisted_after_pass(job_db, tmp_path) -> None:
    """Every completed pass advances the persisted watermark (#357): the
    in-memory cursor and the ``global_settings`` document carry the same
    anchor (scan start minus the overlap window)."""
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    broker = AgentExecutionBroker(TEST_DATABASE_URL, bundle_dir=bundle_dir, data_dir=tmp_path)

    before = datetime.now(UTC) - reaper._REAP_OVERLAP
    broker.reap_terminal_bundles()
    after = datetime.now(UTC) - reaper._REAP_OVERLAP

    assert broker._reap_watermark is not None
    stored = _persisted_watermark(job_db)
    assert stored is not None
    assert before <= stored <= after
    assert stored == broker._reap_watermark


def test_restart_resumes_from_persisted_watermark(job_db, tmp_path) -> None:
    """Acceptance 1: a restarted process must NOT rescan all terminal rows.
    With a persisted watermark and old terminal rows outside the overlap
    window, the first pass of a fresh broker uses the indexed incremental
    query (finished_at >= watermark), so old bundles survive and only rows
    inside the window are reaped."""
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    first = AgentExecutionBroker(TEST_DATABASE_URL, bundle_dir=bundle_dir, data_dir=tmp_path)
    first.reap_terminal_bundles()  # sets AND persists the watermark
    stored = _persisted_watermark(job_db)
    assert stored is not None

    # History rows: finished long before the persisted watermark. A restarted
    # full scan (the pre-#357 behavior) would reap both bundles.
    for exec_id in ("old-done", "old-cancelled"):
        (bundle_dir / f"{exec_id}.tar.gz").write_bytes(b"bundle")
        _insert_request(
            job_db,
            execution_id=exec_id,
            state="done" if exec_id.endswith("done") else "cancelled",
            bundle_name=f"{exec_id}.tar.gz",
            finished_at=stored - timedelta(days=1),
        )
    # A fresh row inside the overlap window: must be reaped.
    (bundle_dir / "fresh.tar.gz").write_bytes(b"bundle")
    _insert_request(
        job_db,
        execution_id="fresh",
        state="done",
        bundle_name="fresh.tar.gz",
        finished_at=datetime.now(UTC),
    )

    restarted = AgentExecutionBroker(TEST_DATABASE_URL, bundle_dir=bundle_dir, data_dir=tmp_path)
    with patch.object(
        DatabaseConnection, "stream", autospec=True, wraps=DatabaseConnection.stream
    ) as stream_spy:
        reaped = restarted.reap_terminal_bundles()

    # Only the overlap-window row was scanned/reaped; the two history rows
    # outside the window survived — proof the first pass was incremental.
    assert reaped == 1
    assert not (bundle_dir / "fresh.tar.gz").exists()
    assert (bundle_dir / "old-done.tar.gz").is_file()
    assert (bundle_dir / "old-cancelled.tar.gz").is_file()
    # The incremental query shape (not the full-scan one) served the pass.
    query = stream_spy.call_args.args[2]
    assert "state='done' and finished_at >= %s" in query
    assert "state in ('done', 'cancelled')" not in query
    # The restored in-memory cursor equals the persisted value.
    assert restarted._reap_watermark is not None
    assert stored <= restarted._reap_watermark


def test_missing_watermark_falls_back_to_full_scan(job_db, tmp_path) -> None:
    """Acceptance 3: with no persisted document (fresh install or deleted
    key) the first pass replays the full scan — same behavior as #139."""
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    old_bundle = bundle_dir / "old.tar.gz"
    old_bundle.write_bytes(b"bundle")
    _insert_request(
        job_db,
        execution_id="old",
        state="done",
        bundle_name="old.tar.gz",
        finished_at=datetime.now(UTC) - timedelta(days=1),
    )
    broker = AgentExecutionBroker(TEST_DATABASE_URL, bundle_dir=bundle_dir, data_dir=tmp_path)

    assert broker.reap_terminal_bundles() == 1
    assert not old_bundle.exists()
    # The fallback pass still persists its watermark for the next restart.
    assert _persisted_watermark(job_db) is not None


def test_corrupt_watermark_falls_back_to_full_scan(job_db, tmp_path) -> None:
    """Acceptance 3: a corrupt document (non-ISO timestamp) must not break
    reaping — the pass degrades to the full scan instead of raising."""
    with job_db.connect() as conn:
        conn.execute(
            "insert into global_settings(key, value) values ('reap_watermark', %s)"
            " on conflict(key) do update set value=excluded.value",
            (json.dumps({"watermark": "not-a-timestamp"}),),
        )
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    stale = bundle_dir / "stale.tar.gz"
    stale.write_bytes(b"bundle")
    _insert_request(
        job_db,
        execution_id="stale",
        state="done",
        bundle_name="stale.tar.gz",
        finished_at=datetime.now(UTC) - timedelta(days=1),
    )
    broker = AgentExecutionBroker(TEST_DATABASE_URL, bundle_dir=bundle_dir, data_dir=tmp_path)

    reaped = broker.reap_terminal_bundles()

    assert reaped == 1
    assert not stale.exists()
    assert broker._reap_watermark is not None
    # The recovered pass overwrites the corrupt document.
    assert _persisted_watermark(job_db) is not None
