"""Shared-database schema guard for init_db (2026-08-27 incident).

A worktree script without .env resolves the code-default DSN — the bare
shared ``agent_legion`` database — and init_db then pushes whatever
migrations the (possibly newer) code carries onto prod. The guard refuses
that path unless the operator explicitly opts in via
AGENT_LEGION_ALLOW_SHARED_DB_SCHEMA.
"""

from __future__ import annotations

import pytest

from server.app.db.schema_guard import (
    SHARED_DB_NAME,
    SharedDatabaseSchemaError,
    dsn_database_name,
    guard_shared_db,
)


@pytest.mark.no_db
def test_dsn_database_name_parses_path() -> None:
    assert dsn_database_name("postgresql://127.0.0.1:5432/agent_legion_dev") == "agent_legion_dev"
    assert dsn_database_name("postgresql://u:p@h:5/db?sslmode=require") == "db"
    assert dsn_database_name("postgresql://127.0.0.1:5432/" + SHARED_DB_NAME) == SHARED_DB_NAME


@pytest.mark.no_db
def test_dsn_database_name_follows_libpq_semantics() -> None:
    """Equivalent DSN forms must not slip past the guard: URL-encoded names
    decode to the shared name, and ?dbname= is a legal override the URL
    path alone would miss (codex review P1)."""
    assert dsn_database_name("postgresql://host/agent%5Flegion") == SHARED_DB_NAME
    assert dsn_database_name("postgresql://host/?dbname=" + SHARED_DB_NAME) == SHARED_DB_NAME
    assert dsn_database_name("postgresql://host/other?dbname=" + SHARED_DB_NAME) == SHARED_DB_NAME
    # And the non-shared lookalikes stay non-shared.
    assert dsn_database_name("postgresql://host/agent%5Flegion%5Fdev") == "agent_legion_dev"


@pytest.mark.no_db
def test_guard_rejects_encoded_and_query_shared_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_LEGION_ALLOW_SHARED_DB_SCHEMA", raising=False)
    for dsn in (
        "postgresql://host/agent%5Flegion",
        "postgresql://host/?dbname=agent_legion",
    ):
        with pytest.raises(SharedDatabaseSchemaError, match="refusing to initialize"):
            guard_shared_db(dsn)


@pytest.mark.no_db
def test_guard_rejects_shared_db_without_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_LEGION_ALLOW_SHARED_DB_SCHEMA", raising=False)
    with pytest.raises(SharedDatabaseSchemaError, match="refusing to initialize"):
        guard_shared_db("postgresql://127.0.0.1:5432/" + SHARED_DB_NAME)


@pytest.mark.no_db
def test_guard_allows_shared_db_with_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LEGION_ALLOW_SHARED_DB_SCHEMA", "1")
    guard_shared_db("postgresql://127.0.0.1:5432/" + SHARED_DB_NAME)  # no raise


@pytest.mark.no_db
def test_guard_ignores_derived_databases(monkeypatch: pytest.MonkeyPatch) -> None:
    """Derived names (test_/e2e_/worktree suffix) never need the opt-in."""
    monkeypatch.delenv("AGENT_LEGION_ALLOW_SHARED_DB_SCHEMA", raising=False)
    guard_shared_db("postgresql://127.0.0.1:5432/agent_legion_test_gw0")  # no raise
    guard_shared_db("postgresql://127.0.0.1:5432/agent_legion_develop")  # no raise
    guard_shared_db("postgresql://127.0.0.1:5432/agent_legion_ci")  # no raise


@pytest.mark.no_db
def test_opt_in_value_must_be_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the exact sentinel unlocks the shared db; fuzzy values stay out."""
    monkeypatch.setenv("AGENT_LEGION_ALLOW_SHARED_DB_SCHEMA", "true")
    with pytest.raises(SharedDatabaseSchemaError):
        guard_shared_db("postgresql://127.0.0.1:5432/" + SHARED_DB_NAME)


@pytest.mark.no_db
def test_init_db_routes_through_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard is on init_db's entry path, not just an internal helper."""
    from server.app.db import schema as schema_module

    monkeypatch.delenv("AGENT_LEGION_ALLOW_SHARED_DB_SCHEMA", raising=False)
    called = []
    monkeypatch.setattr(schema_module, "guard_shared_db", lambda dsn: called.append(dsn))

    class _Boom(Exception):
        pass

    def _refuse(dsn):
        raise _Boom

    monkeypatch.setattr(schema_module, "write_transaction", _refuse)
    with pytest.raises(_Boom):
        schema_module.init_db("postgresql://127.0.0.1:5432/agent_legion_dev")
    assert called == ["postgresql://127.0.0.1:5432/agent_legion_dev"]
