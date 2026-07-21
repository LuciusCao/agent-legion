from __future__ import annotations

from pathlib import Path

import pytest

from server.app.db.schema import init_db
from server.app.worker_control import WorkspaceWorkerControl
from tests.postgres_support import TEST_DATABASE_URL


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = TEST_DATABASE_URL
    init_db(path)
    return path


def test_default_is_paused(db_path):
    control = WorkspaceWorkerControl(db_path=db_path)
    assert control.is_paused("ws1") is True


def test_pause_state_visible_across_instances(db_path):
    a = WorkspaceWorkerControl(db_path=db_path)
    a.resume("ws1")
    b = WorkspaceWorkerControl(db_path=db_path)  # 模拟另一进程/重启
    assert b.is_paused("ws1") is False
    b.pause("ws1")
    assert a.is_paused("ws1") is True


def test_updated_by_recorded(db_path):
    a = WorkspaceWorkerControl(db_path=db_path, process_id="host-a:123")
    a.pause("ws1")
    from server.app.db.transaction import read_connection

    with read_connection(db_path) as conn:
        row = conn.execute("select scope, paused, updated_by from worker_control_state").fetchone()
    assert row["scope"] == "workspace:ws1"
    assert row["paused"] == 1
    assert row["updated_by"] == "host-a:123"


def test_memory_only_fallback_unchanged():
    control = WorkspaceWorkerControl()
    assert control.is_paused("ws1") is True
    control.resume("ws1")
    assert control.is_paused("ws1") is False


def test_persist_pause_retries_on_transaction_conflict(db_path, monkeypatch):
    from psycopg.errors import SerializationFailure

    import server.app.worker_control as control_module
    from server.app.db.transaction import write_transaction as real_write_transaction

    calls = 0

    def flaky_write_transaction(path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SerializationFailure("serialization failure")
        return real_write_transaction(path)

    monkeypatch.setattr(control_module, "write_transaction", flaky_write_transaction)
    control = WorkspaceWorkerControl(db_path=db_path)
    control.pause("ws1")
    assert calls == 2
    assert control.is_paused("ws1") is True
