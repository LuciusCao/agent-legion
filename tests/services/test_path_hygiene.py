"""Startup path-hygiene report and the legacy-absolute warning (issue #37).

DB path columns must hold data-dir-relative paths only; the startup report
surfaces legacy absolute rows (bare-metal era) so a deployment shape change
is noticed before executions stall.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from server.app.services.path_hygiene import (
    count_absolute_db_paths,
    report_absolute_db_paths,
    report_absolute_db_paths_background,
    warn_legacy_absolute,
)


def _seed(job_db, *, job_id: str, log_path: str, run_dir: str, storage_dir: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('ws-path', 'Test', 'demo_workflow') on conflict(id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id, storage_dir)"
            " values (%s, 'ws-path', 'questions', 'question', %s, %s)",
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
    """Lifespan startup wires the background variant, never the sync scan."""
    from server.app import worker_startup

    settings.executor_runtime.workflows.enabled = False
    calls: list = []
    monkeypatch.setattr(
        worker_startup,
        "report_absolute_db_paths_background",
        calls.append,
    )

    worker_startup.start_worker_threads(
        settings,
        job_db=None,
        executor_leases=None,
        agent_broker=None,
        workspace_worker_control=None,
        agent_manager=None,
        agent_dispatch=None,
    )

    assert len(calls) == 1
