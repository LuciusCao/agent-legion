"""Shared-database schema guard (2026-08-27 incident).

A worktree script without .env resolves the code-default DSN — the bare
shared ``agent_legion`` database — and init_db then pushes whatever
migrations the (possibly newer) code carries onto prod. This module owns
the refusal so ``schema.py`` stays focused on the migration mechanics.
"""

from __future__ import annotations

import os

import psycopg.conninfo

from server.app.db.connection import DatabaseDsn

# The bare shared/prod database name: the code default when
# AGENT_LEGION_DATABASE_URL is unset. Every derived database carries a
# suffix (test_<worktree>, e2e_<slug>, or the worktree name itself), so an
# exact match means "this process fell back to the default DSN".
SHARED_DB_NAME = "agent_legion"
_SHARED_DB_SCHEMA_ENV = "AGENT_LEGION_ALLOW_SHARED_DB_SCHEMA"


class SharedDatabaseSchemaError(RuntimeError):
    """init_db refused to touch the shared database without explicit consent."""


def dsn_database_name(database_dsn: DatabaseDsn) -> str:
    """The database name libpq would actually connect to.

    Parsed with psycopg's conninfo (libpq semantics), not a plain URL path:
    ``agent%5Flegion`` URL-decodes to the shared name and
    ``?dbname=agent_legion`` is a legal DSN form that must not slip past
    the guard. conninfo_to_dict does not read the libpq environment, so a
    dbname-less DSN falls back to PGDATABASE — the env libpq itself would
    apply at connect time.
    """
    parsed = psycopg.conninfo.conninfo_to_dict(str(database_dsn))
    return str(parsed.get("dbname") or os.environ.get("PGDATABASE") or "")


def guard_shared_db(database_dsn: DatabaseDsn) -> None:
    """Refuse to migrate the bare shared database without explicit consent.

    The intended operator (prod server, deploy tooling) sets
    AGENT_LEGION_ALLOW_SHARED_DB_SCHEMA=1; anything else resolving the
    default DSN is a misdirected process (worktree script without .env,
    CI export_openapi) and fails with remediation guidance instead of
    pushing unreleased migrations onto the shared database.
    """
    if dsn_database_name(database_dsn) != SHARED_DB_NAME:
        return
    if os.environ.get(_SHARED_DB_SCHEMA_ENV) == "1":
        return
    raise SharedDatabaseSchemaError(
        f"refusing to initialize/migrate the shared database "
        f"'{SHARED_DB_NAME}': this DSN matches the code default, which "
        "usually means AGENT_LEGION_DATABASE_URL is unset (no .env) and a "
        "tool fell back to the shared/prod database. Set "
        "AGENT_LEGION_DATABASE_URL to a dedicated database, or set "
        f"{_SHARED_DB_SCHEMA_ENV}=1 if migrating the shared database is "
        "really intended."
    )


def refuse_shared_db_exit(database_dsn: DatabaseDsn) -> None:
    """CLI helper: SystemExit (not the init_db exception) when the resolved
    DSN is the shared database — for tools that only need a disposable
    database and must not build the app against prod at all (export_openapi,
    2026-08-27 incident).
    """
    if dsn_database_name(database_dsn) == SHARED_DB_NAME:
        raise SystemExit(
            "refusing to run: the resolved database is the shared "
            f"'{SHARED_DB_NAME}' (AGENT_LEGION_DATABASE_URL unset or "
            "pointing at it). This tool only needs a disposable database — "
            "point AGENT_LEGION_DATABASE_URL at a worktree/derived database "
            "(scripts/init-worktree.sh sets one) instead of letting it fall "
            "back to the shared one."
        )
