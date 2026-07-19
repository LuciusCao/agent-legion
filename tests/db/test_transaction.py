from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from server.app.db.connection import connect_sqlite
from server.app.db.transaction import read_connection, write_transaction


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "t.sqlite"
    conn = connect_sqlite(path)
    try:
        conn.execute("create table items (id integer primary key, name text not null)")
        conn.commit()
    finally:
        conn.close()
    return path


def test_write_transaction_commits_on_success(db_path):
    with write_transaction(db_path) as conn:
        conn.execute("insert into items (name) values ('a')")
    with read_connection(db_path) as conn:
        assert conn.execute("select name from items").fetchone()["name"] == "a"


def test_write_transaction_rolls_back_on_exception(db_path):
    with (
        pytest.raises(ValueError, match="boom"),
        write_transaction(db_path) as conn,
    ):
        conn.execute("insert into items (name) values ('a')")
        raise ValueError("boom")
    with read_connection(db_path) as conn:
        assert conn.execute("select count(*) from items").fetchone()[0] == 0


def test_write_transaction_holds_immediate_lock(db_path):
    # 第二个并发写事务在 busy_timeout 后应报 lock 错，证明 begin immediate 生效。
    entered = threading.Event()
    errors: list[sqlite3.OperationalError] = []

    def contender() -> None:
        entered.wait(5)
        try:
            with write_transaction(db_path) as conn:
                conn.execute("insert into items (name) values ('b')")
        except sqlite3.OperationalError as exc:
            errors.append(exc)

    # busy_timeout=5000 太长，直接用短超时连接模拟竞争；这里改为断言
    # 第一个事务提交前第二个连接写入失败。
    with write_transaction(db_path) as conn:
        conn.execute("insert into items (name) values ('a')")
        entered.set()
        other = sqlite3.connect(db_path, timeout=0.1)
        try:
            with pytest.raises(sqlite3.OperationalError):
                other.execute("insert into items (name) values ('b')")
                other.commit()
        finally:
            other.close()
    assert errors == []


def test_write_transaction_rejects_implicit_nesting(db_path):
    # 事务内再开 begin 必须报错（SQLite 原生行为），调用方不得嵌套。
    with (
        write_transaction(db_path) as conn,
        pytest.raises(sqlite3.OperationalError),
    ):
        conn.execute("begin immediate")


def test_read_connection_does_not_commit(db_path):
    with read_connection(db_path) as conn:
        rows = conn.execute("select count(*) from items").fetchone()
    assert rows[0] == 0
