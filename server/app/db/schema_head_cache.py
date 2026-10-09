"""Process-local memo for ``init_db``'s steady-state head check.

Once ``init_db`` has verified a database at the current ``SCHEMA_VERSION``
in this process, replaying the full advisory-locked migration transaction
on every repeat (each ``JobQueries`` construction runs one) is pure
overhead: the early return (``max(applied) >= SCHEMA_VERSION``) makes those
repeats no-ops. Another process migrating the database to a NEWER version
early-returns through the same comparison, so skipping repeat work is
semantically identical to running it.

The only way a verified database falls behind again is an in-process schema
rebuild or rewind, which today exists only in the test harness:
``tests/conftest.py`` calls ``note_schema_rebuilt`` when it drops and
recreates the per-worker schema, and wraps ``fresh_schema`` tests (which
rewind ``schema_migrations`` mid-test and expect the next ``init_db`` to
upgrade again) in ``init_db_full_check`` so the memo never short-circuits
them. ``guard_shared_db`` is a pure per-DSN string check and still runs on
every ``init_db`` call, memo hit or not.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from server.app.db.connection import DatabaseDsn

_VERIFIED_AT_HEAD: dict[DatabaseDsn, int] = {}
_LOCK = threading.Lock()
_FULL_CHECK_DEPTH = 0


def verified_at_head(database_dsn: DatabaseDsn, schema_version: int) -> bool:
    """True when this process already verified the DSN at ``schema_version``."""
    with _LOCK:
        return _FULL_CHECK_DEPTH == 0 and _VERIFIED_AT_HEAD.get(database_dsn, 0) >= schema_version


def note_verified_at_head(database_dsn: DatabaseDsn, schema_version: int) -> None:
    """Record a successful head verification; a no-op inside a full-check window."""
    with _LOCK:
        if _FULL_CHECK_DEPTH == 0:
            _VERIFIED_AT_HEAD[database_dsn] = schema_version


def note_schema_rebuilt(database_dsn: DatabaseDsn | None = None) -> None:
    """Invalidate memoized head state after a schema rebuild (test harness)."""
    with _LOCK:
        if database_dsn is None:
            _VERIFIED_AT_HEAD.clear()
        else:
            _VERIFIED_AT_HEAD.pop(database_dsn, None)


@contextmanager
def init_db_full_check() -> Iterator[None]:
    """Force every ``init_db`` call inside the window through the full path.

    Process-wide (pytest runs one test per worker process at a time), so
    ``init_db`` calls from app request threads of the same test are covered
    too. Nothing is recorded inside the window: a mid-window success must
    not be trusted after a later in-window rewind.
    """
    global _FULL_CHECK_DEPTH
    with _LOCK:
        _FULL_CHECK_DEPTH += 1
    try:
        yield
    finally:
        with _LOCK:
            _FULL_CHECK_DEPTH -= 1
