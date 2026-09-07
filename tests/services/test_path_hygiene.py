"""Startup path-hygiene report and the legacy-absolute warning (issue #37).

DB path columns must hold data-dir-relative paths only; the startup report
surfaces legacy absolute rows (bare-metal era) so a deployment shape change
is noticed before executions stall. #521 adds the per-path warn dedupe and
the one-time rewrite that retires the legacy rows outright.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from server.app.services.path_hygiene import (
    count_absolute_db_paths,
    migrate_absolute_db_paths,
    migrate_absolute_db_paths_background,
    report_absolute_db_paths,
    report_absolute_db_paths_background,
    reset_legacy_absolute_dedupe,
    warn_legacy_absolute,
)


def _seed(job_db, *, job_id: str, log_path: str, run_dir: str, storage_dir: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('ws-path', 'Test', 'demo_workflow') on conflict(id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, storage_dir)"
            " values (%s, 'ws-path', 'question', %s, %s)",
            (job_id, job_id, storage_dir),
        )
        conn.execute(
            "insert into node_runs(job_id, node_key, status, log_path, run_dir)"
            " values (%s, 'generate', 'running', %s, %s)",
            (job_id, log_path, run_dir),
        )


def test_count_absolute_db_paths_clean(job_db) -> None:
    _seed(
        job_db,
        job_id="job-clean",
        log_path="logs/jobs/job-clean-generate.log",
        run_dir="jobs/ws/job-clean/runs/generate/w",
        storage_dir="jobs/ws/job-clean",
    )

    assert count_absolute_db_paths(job_db) == {
        "log_path": 0,
        "run_dir": 0,
        "session_dir": 0,
        "jobs.storage_dir": 0,
    }


def test_count_absolute_db_paths_flags_every_column(job_db) -> None:
    _seed(
        job_db,
        job_id="job-legacy",
        log_path="/Users/x/data/logs/jobs/job-legacy-generate.log",
        run_dir="/Users/x/data/jobs/ws/job-legacy/runs/generate/w",
        storage_dir="/Users/x/data/jobs/ws/job-legacy",
    )

    counts = count_absolute_db_paths(job_db)
    assert counts["log_path"] == 1
    assert counts["run_dir"] == 1
    assert counts["session_dir"] == 0
    assert counts["jobs.storage_dir"] == 1


def test_report_absolute_db_paths_warns_only_when_dirty(job_db, caplog) -> None:
    _seed(
        job_db,
        job_id="job-legacy",
        log_path="/Users/x/data/logs/jobs/job-legacy-generate.log",
        run_dir="",
        storage_dir="",
    )

    with caplog.at_level(logging.WARNING, logger="server.app.services.path_hygiene"):
        counts = report_absolute_db_paths(job_db)

    assert counts["log_path"] == 1
    messages = [record.getMessage() for record in caplog.records]
    assert any("legacy absolute paths" in message for message in messages)
    assert any("log_path=1" in message for message in messages)


def test_report_absolute_db_paths_stays_quiet_when_clean(job_db, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="server.app.services.path_hygiene"):
        report_absolute_db_paths(job_db)

    assert not caplog.records


def test_warn_legacy_absolute_logs_and_warns(caplog) -> None:
    with (
        caplog.at_level(logging.WARNING, logger="server.app.services.path_hygiene"),
        pytest.warns(DeprecationWarning, match="Legacy absolute path stored"),
    ):
        warn_legacy_absolute()

    assert any("Legacy absolute path stored" in record.getMessage() for record in caplog.records)


def test_report_background_returns_while_scan_is_blocked(monkeypatch) -> None:
    """Issue #106: the startup report must run off the lifespan startup path."""
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def _blocking_report(db) -> None:
        entered.set()
        release.wait(30)
        finished.set()

    monkeypatch.setattr(
        "server.app.services.path_hygiene.report_absolute_db_paths", _blocking_report
    )
    started = time.monotonic()
    report_absolute_db_paths_background(db=None)
    elapsed = time.monotonic() - started

    assert entered.wait(5), "report thread never started"
    assert elapsed < 5, "background report blocked the caller (a sync call waits 30s)"
    release.set()
    assert finished.wait(5)


def test_report_background_logs_failures_without_raising(monkeypatch, caplog) -> None:
    """A failing scan must surface in logs, never abort app startup."""
    failed = threading.Event()

    def _boom(db) -> None:
        failed.set()
        raise RuntimeError("db gone")

    monkeypatch.setattr("server.app.services.path_hygiene.report_absolute_db_paths", _boom)
    with caplog.at_level(logging.ERROR, logger="server.app.services.path_hygiene"):
        report_absolute_db_paths_background(db=None)
        assert failed.wait(5)
        deadline = time.monotonic() + 5
        while not caplog.records and time.monotonic() < deadline:
            time.sleep(0.01)

    assert any("path-hygiene startup report failed" in r.getMessage() for r in caplog.records)


def test_start_worker_threads_kicks_background_report(settings, monkeypatch) -> None:
    """Lifespan startup wires the background variant, never the sync scan.

    The retired workflows.enabled short-circuit used to return before any
    assembly; with it gone (#385/#389) the report still fires first, before
    the (stubbed) sweeper/worker assembly runs."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from server.app import worker_startup

    report_calls: list = []
    rewrite_calls: list = []
    monkeypatch.setattr(
        worker_startup,
        "report_absolute_db_paths_background",
        report_calls.append,
    )
    monkeypatch.setattr(
        worker_startup,
        "migrate_absolute_db_paths_background",
        lambda db, data_dir: rewrite_calls.append((db, data_dir)),
    )
    monkeypatch.setattr(worker_startup, "CodeDispatchService", MagicMock())

    worker_startup.start_worker_threads(
        settings,
        job_db=None,
        executor_leases=MagicMock(),
        agent_broker=MagicMock(),
        workspace_worker_control=None,
        agent_manager=None,
        agent_dispatch=SimpleNamespace(skill_manager=MagicMock(), artifact_store=MagicMock()),
    )

    assert len(report_calls) == 1
    # #521: the one-time rewrite kicks on the same off-readiness contract.
    assert len(rewrite_calls) == 1


# --- #521: per-path warn dedupe ---------------------------------------------


def test_warn_legacy_absolute_dedupes_per_stored_path(caplog) -> None:
    """Same stored path warns once per process; distinct paths each warn."""
    reset_legacy_absolute_dedupe()
    with caplog.at_level(logging.WARNING, logger="server.app.services.path_hygiene"):
        warn_legacy_absolute("/old/data/logs/a.log")
        warn_legacy_absolute("/old/data/logs/a.log")
        warn_legacy_absolute("/old/data/logs/b.log")
    reset_legacy_absolute_dedupe()
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 2, messages


def test_warn_legacy_absolute_empty_key_never_dedupes(caplog) -> None:
    """Direct callers without a key keep warning every call (legacy shape)."""
    reset_legacy_absolute_dedupe()
    with caplog.at_level(logging.WARNING, logger="server.app.services.path_hygiene"):
        warn_legacy_absolute()
        warn_legacy_absolute()
    reset_legacy_absolute_dedupe()
    assert len(caplog.records) == 2


def test_resolve_data_path_dedupes_repeated_legacy_reads(tmp_path, caplog) -> None:
    """The hot-path shape #521 targets: the same stored legacy row resolved
    repeatedly (result commit, claim, dashboard) logs once per process."""
    from server.app.storage_paths import resolve_data_path

    data_dir = tmp_path / "data"
    (data_dir / "logs").mkdir(parents=True)
    legacy = tmp_path / "old" / "data" / "logs" / "x.log"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("x", encoding="utf-8")

    reset_legacy_absolute_dedupe()
    with caplog.at_level(logging.WARNING, logger="server.app.services.path_hygiene"):
        for _ in range(5):
            resolved = resolve_data_path(str(legacy), data_dir, allow_missing=True)
            assert resolved.name == "x.log"
    reset_legacy_absolute_dedupe()
    # The log emit deduped to one; the resolution itself is unaffected.
    assert len(caplog.records) == 1, [r.getMessage() for r in caplog.records]


# --- #521: one-time legacy-absolute rewrite ----------------------------------


def test_migrate_absolute_db_paths_rebases_and_converges_to_clean(job_db, tmp_path) -> None:
    _seed(
        job_db,
        job_id="job-legacy",
        log_path="/srv/old/data/logs/jobs/job-legacy-generate.log",
        run_dir="/srv/old/data/jobs/ws/job-legacy/runs/generate/w",
        storage_dir="/srv/old/data/jobs/ws/job-legacy",
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    migrated = migrate_absolute_db_paths(job_db, data_dir)

    assert migrated == {
        "node_runs.log_path": 1,
        "node_runs.run_dir": 1,
        "jobs.storage_dir": 1,
    }
    with job_db.connect() as conn:
        log_path = conn.execute(
            "select log_path from node_runs where job_id='job-legacy'"
        ).fetchone()["log_path"]
        storage_dir = conn.execute("select storage_dir from jobs where id='job-legacy'").fetchone()[
            "storage_dir"
        ]
    assert log_path == "logs/jobs/job-legacy-generate.log"
    assert storage_dir == "jobs/ws/job-legacy"
    # The startup report converges to zero — the #521 acceptance signal.
    assert count_absolute_db_paths(job_db) == {
        "log_path": 0,
        "run_dir": 0,
        "session_dir": 0,
        "jobs.storage_dir": 0,
    }


def test_migrate_absolute_db_paths_leaves_unmappable_rows(job_db, tmp_path) -> None:
    """A path with no <data-dir-name>/<category>/ suffix survives untouched
    and stays visible in the report (fail-closed reads keep handling it)."""
    _seed(
        job_db,
        job_id="job-unmapped",
        log_path="/elsewhere/completely/unrelated.log",
        run_dir="",
        storage_dir="",
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    migrated = migrate_absolute_db_paths(job_db, data_dir)

    assert migrated == {}
    assert count_absolute_db_paths(job_db)["log_path"] == 1


def test_migrate_absolute_db_paths_is_idempotent(job_db, tmp_path) -> None:
    _seed(
        job_db,
        job_id="job-legacy",
        log_path="/srv/old/data/logs/jobs/job-legacy-generate.log",
        run_dir="",
        storage_dir="",
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    first = migrate_absolute_db_paths(job_db, data_dir)
    second = migrate_absolute_db_paths(job_db, data_dir)

    assert first == {"node_runs.log_path": 1}
    # A clean database is a no-op: the selection itself is the guard.
    assert second == {}


def test_rewrite_background_logs_failures_without_raising(monkeypatch, caplog) -> None:
    """A failing rewrite must surface in logs, never abort app startup."""
    failed = threading.Event()

    def _boom(db, data_dir) -> None:
        failed.set()
        raise RuntimeError("db gone")

    monkeypatch.setattr("server.app.services.path_hygiene.migrate_absolute_db_paths", _boom)
    with caplog.at_level(logging.ERROR, logger="server.app.services.path_hygiene"):
        migrate_absolute_db_paths_background(db=None, data_dir=None)
        assert failed.wait(5)
        deadline = time.monotonic() + 5
        while not caplog.records and time.monotonic() < deadline:
            time.sleep(0.01)

    assert any("path-hygiene one-time rewrite failed" in r.getMessage() for r in caplog.records)
