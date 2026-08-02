from __future__ import annotations

import contextlib
import os
import re
from pathlib import Path
from urllib.parse import quote


def _worker_schema() -> str:
    worker = re.sub(r"[^a-zA-Z0-9_]", "_", os.environ.get("PYTEST_XDIST_WORKER", "main"))
    return f"agent_legion_test_{worker}"


def _worktree_database_name() -> str:
    # Isolate the test database per worktree: every worktree gets its own
    # database derived from its directory name, so concurrent test runs from
    # different worktrees can no longer drop each other's schemas.
    slug = re.sub(r"[^a-zA-Z0-9_]", "_", Path(__file__).resolve().parents[1].name).lower()
    return f"agent_legion_test_{slug}"


# Tests must never fall back to the ambient AGENT_LEGION_DATABASE_URL: that var
# points at the dev/prod database in real shells, and agent loops exporting it
# have already wiped dev-schema state by running the suite against it. Only an
# explicit AGENT_LEGION_TEST_DATABASE_URL may redirect the test database.
BASE_DATABASE_URL = os.environ.get(
    "AGENT_LEGION_TEST_DATABASE_URL",
    f"postgresql://127.0.0.1:5432/{_worktree_database_name()}",
)
TEST_SCHEMA = _worker_schema()
separator = "&" if "?" in BASE_DATABASE_URL else "?"
TEST_DATABASE_URL = (
    f"{BASE_DATABASE_URL}{separator}options={quote(f'-csearch_path={TEST_SCHEMA}', safe='')}"
)


def ensure_test_database() -> None:
    """Create the per-worktree test database on first use.

    New worktrees get a dedicated database name (see `_worktree_database_name`),
    which would otherwise require a manual `createdb` before the first test run.
    Connection-time errors do not carry a sqlstate, so instead of probing for
    the database we simply issue CREATE DATABASE and ignore DuplicateDatabase.
    """
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(BASE_DATABASE_URL)
    dbname = params.pop("dbname", None)
    if not dbname:
        return
    with (
        psycopg.connect(make_conninfo(**params, dbname="postgres"), autocommit=True) as conn,
        contextlib.suppress(psycopg.errors.DuplicateDatabase),
    ):
        conn.execute(sql.SQL("create database {}").format(sql.Identifier(dbname)))


# Importing test helpers must remain side-effect free. The root PostgreSQL
# session fixture creates the per-worktree database only when a test is marked
# ``postgres``; pure collection and unit runs must work with PostgreSQL offline.
