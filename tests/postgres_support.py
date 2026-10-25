from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import quote


def _worker_schema() -> str:
    worker = re.sub(r"[^a-zA-Z0-9_]", "_", os.environ.get("PYTEST_XDIST_WORKER", "main"))
    return f"agent_legion_test_{worker}"


def _worktree_database_name() -> str:
    # Isolate the test database per worktree. Tower children include the parent
    # name because their short wt-* names are unique only within one parent.
    worktree = Path(__file__).resolve().parents[1]
    slug_source = worktree.name
    if (
        worktree.name.startswith("wt-")
        and len(worktree.name) > 3
        and worktree.parent.name == "worktrees"
        and worktree.parent.parent.name == ".tower"
    ):
        slug_source = f"{worktree.parents[2].name}_{worktree.name}"
    slug = re.sub(r"[^a-zA-Z0-9_]", "_", slug_source).lower()
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
    Every xdist worker owns a session fixture, so first use can be concurrent.
    A session-level advisory lock serializes the catalog check and CREATE;
    closing the maintenance connection releases the lock automatically.
    """
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(BASE_DATABASE_URL)
    dbname = params.pop("dbname", None)
    if not dbname:
        return
    with psycopg.connect(make_conninfo(**params, dbname="postgres"), autocommit=True) as conn:
        conn.execute("select pg_advisory_lock(hashtext(%s))", (dbname,))
        exists = conn.execute("select 1 from pg_database where datname = %s", (dbname,)).fetchone()
        if exists is None:
            conn.execute(sql.SQL("create database {}").format(sql.Identifier(dbname)))
        # Role-isolation guard (scripts/drop-worktree-db.sh): align the
        # derived database owner with agent_legion_dev when the role exists,
        # so the cleanup path can run as a non-superuser role. Best-effort:
        # a creator without ownership-transfer rights simply skips.
        try:
            role = conn.execute(
                "select 1 from pg_roles where rolname = 'agent_legion_dev'"
            ).fetchone()
            if role is not None:
                conn.execute(
                    sql.SQL("alter database {} owner to agent_legion_dev").format(
                        sql.Identifier(dbname)
                    )
                )
        except Exception:
            pass


# Pool settle/close for test boundaries lives in production since #1077:
# ``server.app.db.pools.settle_database_pools`` (per-test isolation barrier,
# pools stay alive) and ``close_database_pools`` (settles before closing —
# schema rebuilds and session teardown use it exactly like the lifespan).


# Importing test helpers must remain side-effect free. The root PostgreSQL
# session fixture creates the per-worktree database only when a test is marked
# ``postgres``; pure collection and unit runs must work with PostgreSQL offline.
