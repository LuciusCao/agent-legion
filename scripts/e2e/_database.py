"""Database handling for the browser smoke E2E runner (Phase 4A).

Dedicated per-worktree E2E database with TRUNCATE-based reset, mirroring
tests/postgres_support.py naming and tests/conftest.py isolation style while
keeping E2E runtime state fully separate from pytest state.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)


def e2e_database_name(project_root: Path) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_]", "_", project_root.name).lower()
    return f"agent_legion_e2e_{slug}"


def admin_dsn() -> str:
    """PostgreSQL admin DSN used for database reset and as the app DSN base.

    Local dev trusts the OS user on 127.0.0.1; CI injects credentials through
    AGENT_LEGION_E2E_ADMIN_DSN (postgres:postgres on the service container).
    """
    return os.environ.get("AGENT_LEGION_E2E_ADMIN_DSN", "postgresql://127.0.0.1:5432/postgres")


def db_dsn(db_name: str) -> str:
    parts = urlsplit(admin_dsn())
    return urlunsplit(parts._replace(path=f"/{db_name}"))


def reset_database(db_name: str) -> None:
    """Reset the E2E database to empty state.

    Creates the database on first use; afterwards wipes all tables with
    TRUNCATE (same isolation style as tests/conftest.py). DROP/CREATE was
    measurably slower (tens of seconds on a loaded machine) because the drop
    forces buffer flushes, while TRUNCATE stays sub-second.
    """
    import psycopg
    from psycopg import sql

    logger.info("Resetting E2E database %s", db_name)
    with psycopg.connect(admin_dsn(), autocommit=True) as conn:
        conn.execute("select pg_advisory_lock(hashtext(%s))", (db_name,))
        exists = conn.execute("select 1 from pg_database where datname = %s", (db_name,)).fetchone()
        if exists is None:
            conn.execute(sql.SQL("create database {}").format(sql.Identifier(db_name)))
            return
    with psycopg.connect(db_dsn(db_name), autocommit=True) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "select tablename from pg_tables where schemaname = 'public'"
            ).fetchall()
        ]
        if not tables:
            return
        conn.execute(
            sql.SQL("truncate table {} restart identity cascade").format(
                sql.SQL(", ").join(sql.Identifier(table) for table in tables)
            )
        )
