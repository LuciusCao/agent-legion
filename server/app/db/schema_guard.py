"""Shared-database schema guard (2026-08-27 incident).

A worktree script without .env resolves the code-default DSN — the bare
shared ``agent_legion`` database — and init_db then pushes whatever
migrations the (possibly newer) code carries onto prod. This module owns
the refusal so ``schema.py`` stays focused on the migration mechanics.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

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
    return urlsplit(str(database_dsn)).path.lstrip("/")


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
