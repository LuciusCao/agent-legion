"""Process-local head memo of ``init_db`` (server/app/db/schema_head_cache.py).

The memo must skip the advisory-locked migration transaction once the DSN
was verified at ``SCHEMA_VERSION`` in this process, must be invalidated by
``note_schema_rebuilt`` (the conftest schema-rebuild hook), and must stay
out of the way inside an ``init_db_full_check`` window (the conftest
fresh_schema wrapper). Fully mocked: no real database involved.
"""

from __future__ import annotations

import pytest

from server.app.db import schema as schema_module
from server.app.db.schema import SCHEMA_VERSION, init_db
from server.app.db.schema_head_cache import init_db_full_check, note_schema_rebuilt

pytestmark = pytest.mark.no_db

_DSN = "postgresql://head-cache-test.invalid/agent_legion_head_cache"


class _FakeResult:
    def fetchall(self) -> list[dict[str, int]]:
        return [{"version": SCHEMA_VERSION}]


class _FakeTransaction:
    def __enter__(self) -> _FakeTransaction:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, *args: object, **kwargs: object) -> _FakeResult:
        return _FakeResult()


@pytest.fixture
def init_db_spy(monkeypatch: pytest.MonkeyPatch):
    spies = {"guard": 0, "transaction": 0}

    def fake_guard(dsn: str) -> None:
        spies["guard"] += 1

    def fake_write_transaction(dsn: str) -> _FakeTransaction:
        spies["transaction"] += 1
        return _FakeTransaction()

    monkeypatch.setattr(schema_module, "guard_shared_db", fake_guard)
    monkeypatch.setattr(schema_module, "write_transaction", fake_write_transaction)
    note_schema_rebuilt(_DSN)
    yield spies
    note_schema_rebuilt(_DSN)


def test_repeat_init_db_skips_the_migration_transaction(init_db_spy) -> None:
    init_db(_DSN)
    init_db(_DSN)

    assert init_db_spy["transaction"] == 1
    # The shared-database guard is a pure string check and still runs on
    # every call, memo hit or not.
    assert init_db_spy["guard"] == 2


def test_note_schema_rebuilt_forces_a_fresh_check(init_db_spy) -> None:
    init_db(_DSN)
    note_schema_rebuilt(_DSN)
    init_db(_DSN)

    assert init_db_spy["transaction"] == 2


def test_full_check_window_disables_the_memo(init_db_spy) -> None:
    init_db(_DSN)
    with init_db_full_check():
        init_db(_DSN)
        init_db(_DSN)
    init_db(_DSN)

    # One real check before the window, two forced ones inside it, and the
    # post-window call hits the memo recorded before the window.
    assert init_db_spy["transaction"] == 3


def test_failed_init_db_is_not_memoized(init_db_spy, monkeypatch: pytest.MonkeyPatch) -> None:
    class _FailingTransaction:
        def __enter__(self) -> _FailingTransaction:
            raise RuntimeError("database unreachable")

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(schema_module, "write_transaction", lambda dsn: _FailingTransaction())
    with pytest.raises(RuntimeError, match="database unreachable"):
        init_db(_DSN)

    monkeypatch.setattr(schema_module, "write_transaction", lambda dsn: _FakeTransaction())
    init_db(_DSN)
    init_db(_DSN)

    # The failure left no memo entry: the retry ran the real check once and
    # only the call after it was served from the memo.
    assert init_db_spy["guard"] == 3
