"""Tests for scripts.backfill_failure_classification with the DB layer fully mocked."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import pytest

from scripts import backfill_failure_classification as bfc

pytestmark = pytest.mark.no_db


class _FakeReadConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.queries: list[str] = []

    def execute(self, sql: str, params: Any = None) -> _FakeReadConn:
        self.queries.append(sql)
        return self

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeCursor:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FakeWriteConn:
    def __init__(self, rowcounts: list[int]) -> None:
        self._rowcounts = iter(rowcounts)
        self.updates: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> _FakeCursor:
        self.updates.append((sql, params))
        return _FakeCursor(next(self._rowcounts))


def _cm(value: Any):
    @contextlib.contextmanager
    def _ctx(dsn: Any) -> Iterator[Any]:
        yield value

    return _ctx


def _run_row(
    run_id: int,
    *,
    failure_category: str = "",
    failure_detail: str = "",
) -> dict[str, Any]:
    return {
        "id": run_id,
        "exit_code": 1,
        "error_message": "boom",
        "failure_category": failure_category,
        "failure_detail": failure_detail,
    }


def _patch_reads(monkeypatch: pytest.MonkeyPatch, conn: _FakeReadConn) -> None:
    monkeypatch.setattr(bfc, "read_connection", _cm(conn))
    monkeypatch.setattr(
        bfc, "classify_failure", lambda exit_code, message: ("crash", "classified detail")
    )


def test_dry_run_reports_matches_without_writing(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_reads(monkeypatch, _FakeReadConn([_run_row(1), _run_row(2)]))

    def _forbidden_write(dsn: Any) -> Any:
        raise AssertionError("write path must not run during --dry-run")

    monkeypatch.setattr(bfc, "write_transaction", _forbidden_write)

    matched, updated = bfc.backfill_failure_classification("dsn", dry_run=True)

    assert (matched, updated) == (2, 0)


def test_dry_run_skips_rows_whose_classification_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_run_row(1, failure_category="crash", failure_detail="classified detail")]
    _patch_reads(monkeypatch, _FakeReadConn(rows))

    matched, updated = bfc.backfill_failure_classification("dsn", dry_run=True)

    assert (matched, updated) == (0, 0)


def test_default_predicate_excludes_unknown_but_include_unknown_adds_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeReadConn([])
    _patch_reads(monkeypatch, conn)

    bfc.backfill_failure_classification("dsn", dry_run=True)
    assert "failure_category=''" in conn.queries[-1]
    assert "unknown" not in conn.queries[-1]

    bfc.backfill_failure_classification("dsn", dry_run=True, include_unknown=True)
    assert "failure_category='unknown'" in conn.queries[-1]


def test_write_path_updates_each_planned_row(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_reads(monkeypatch, _FakeReadConn([_run_row(7), _run_row(8)]))
    write_conn = _FakeWriteConn(rowcounts=[1, 0])
    monkeypatch.setattr(bfc, "write_transaction", _cm(write_conn))

    matched, updated = bfc.backfill_failure_classification("dsn", dry_run=False)

    assert (matched, updated) == (2, 1)
    assert len(write_conn.updates) == 2
    first_sql, first_params = write_conn.updates[0]
    assert "update node_runs" in first_sql
    assert first_params == ("crash", "classified detail", 7)
    # The UPDATE re-checks the predicates so concurrently classified rows are left alone.
    assert "status='failed'" in first_sql
    assert "failure_category=''" in first_sql
