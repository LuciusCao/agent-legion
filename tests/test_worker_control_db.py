from __future__ import annotations

from pathlib import Path

import pytest

from server.app.db.schema import init_db
from server.app.worker_control import WorkspaceWorkerControl


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "jobs.sqlite"
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
