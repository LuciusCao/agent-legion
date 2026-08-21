"""Tests for scripts.trim_terminal_code_manifests (issue #142 生产止血).

The script slims terminal legacy kind='code' manifests to the lightweight
audit stub in bounded batches; this test covers the batching/loop and
dry-run reporting with the DB layer fully mocked.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import trim_terminal_code_manifests as tcm

pytestmark = pytest.mark.no_db


class _FakeRows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchone(self) -> dict[str, Any]:
        return self._rows[0]


class _FakeConn:
    """Queue-driven fake: each UPDATE consumes the next rowcount in the queue;
    a full batch (== batch_size) makes the loop continue, a short one stops it."""

    def __init__(self, *, count: int = 0, rowcounts: list[int] | None = None) -> None:
        self._count = count
        self._rowcounts = list(rowcounts or [])
        self.executions: list[str] = []

    def execute(self, sql: str, params: Any = None) -> Any:
        self.executions.append(sql)
        if "select count(*)" in sql:
            return _FakeRows([{"cnt": self._count}])
        rowcount = self._rowcounts.pop(0) if self._rowcounts else 0
        return _FakeExec(rowcount)


class _FakeExec:
    def __init__(self, rowcount: int = 0) -> None:
        self.rowcount = rowcount


def _ctx(conn: _FakeConn):
    @contextlib.contextmanager
    def _cm(dsn: Any) -> Iterator[Any]:
        yield conn

    return _cm


def test_dry_run_reports_count_without_updating(monkeypatch) -> None:
    conn = _FakeConn(count=7)
    monkeypatch.setattr(tcm, "write_transaction", _ctx(conn))

    assert tcm.trim_terminal_code_manifests("dsn", dry_run=True) == 7
    assert all("update" not in sql.lower() for sql in conn.executions)


def test_trim_loops_in_bounded_batches(monkeypatch) -> None:
    # First UPDATE returns a full batch (500) -> the loop continues; the
    # second returns a short batch (3) -> the loop stops.
    conn = _FakeConn(rowcounts=[500, 3])
    monkeypatch.setattr(tcm, "write_transaction", _ctx(conn))

    assert tcm.trim_terminal_code_manifests("dsn", batch_size=500) == 503

    updates = [sql for sql in conn.executions if "update" in sql.lower()]
    assert len(updates) == 2
    assert "limit 500" in updates[0]
    # The batch query only targets terminal code rows still carrying the
    # heavy embedded runtime_context.job.
    assert "kind = 'code'" in updates[0]
    assert "state in ('done', 'cancelled')" in updates[0]


def test_main_reports_slimmed_and_vacuum_step(monkeypatch, capsys) -> None:
    monkeypatch.setattr(tcm, "load_settings", lambda: SimpleNamespace(database_url="dsn"))
    monkeypatch.setattr(
        tcm,
        "trim_terminal_code_manifests",
        lambda dsn, dry_run=False, batch_size=500: 0,
    )
    monkeypatch.setattr(sys, "argv", ["trim_terminal_code_manifests"])

    tcm.main()

    out = capsys.readouterr().out
    assert "slimmed 0" in out
    assert "VACUUM FULL" in out
