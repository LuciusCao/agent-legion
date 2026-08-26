"""Schema v58: retire all-workspaces register tokens (issue #35).

Registration is scoped-token-only now; the migration revokes any legacy
``agent_register_tokens`` row with ``workspace_id IS NULL`` still marked
live. The registry test also pins SCHEMA_VERSION and its recorded name,
replacing the pin previously held by tests/db/test_studio_chat_schema.py.
"""

from __future__ import annotations

import hashlib
import secrets

from server.app.db.schema import SCHEMA_VERSION
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _seed_workspace(conn, workspace_id: str) -> None:
    conn.execute(
        "insert into workspaces(id, name, default_workflow_key)"
        " values (%s, %s, 'question_comprehension_info') on conflict do nothing",
        (workspace_id, workspace_id),
    )


def _insert_register_token(
    conn,
    token_id: str,
    workspace_id: str | None,
    *,
    revoked: bool = False,
) -> str:
    """Insert one agent_register_tokens row; returns the plaintext token."""
    plaintext = f"{token_id}.{secrets.token_urlsafe(24)}"
    token_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    conn.execute(
        "insert into agent_register_tokens(id, token_hash, workspace_id, label,"
        " revoked_at) values (%s, %s, %s, %s,"
        " case when %s then current_timestamp else null end)",
        (token_id, token_hash, workspace_id, token_id, revoked),
    )
    return plaintext


def test_schema_version_pin() -> None:
    # The latest-migration record pin moved here from test_studio_chat_schema.py
    # (v57 studio_chat_draft → v58 retire_global_register_tokens).
    assert SCHEMA_VERSION == 58
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select name from schema_migrations where version=%s", (SCHEMA_VERSION,)
        ).fetchone()
    assert row is not None
    assert row["name"] == "retire_global_register_tokens"


def test_migration_revokes_only_live_all_workspaces_tokens() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "retire-tokens-ws")
        # A live all-workspaces token (the retired variant) and an already
        # revoked one; plus a live workspace-scoped token that must survive.
        _insert_register_token(conn, "legacy-live", None)
        _insert_register_token(conn, "legacy-dead", None, revoked=True)
        scoped = _insert_register_token(conn, "scoped-live", "retire-tokens-ws")

    from server.app.db.migrations.retire_global_register_tokens import (
        migrate_retire_global_register_tokens,
    )

    # Replays twice: the migration is idempotent and must not touch scoped rows.
    for _ in range(2):
        with write_transaction(TEST_DATABASE_URL) as conn:
            migrate_retire_global_register_tokens(conn)

    with read_connection(TEST_DATABASE_URL) as conn:
        rows = {
            row["id"]: row["revoked_at"]
            for row in conn.execute(
                "select id, revoked_at from agent_register_tokens"
                " where id in ('legacy-live', 'legacy-dead', 'scoped-live')"
            ).fetchall()
        }
    assert rows["legacy-live"] is not None, "live NULL-workspace token must be revoked"
    assert rows["legacy-dead"] is not None
    assert rows["scoped-live"] is None, "workspace-scoped token must survive"

    # The revoked all-workspaces token no longer authenticates a registration.
    from server.app.agent_workers import AgentWorkerRegistry

    scope = AgentWorkerRegistry(TEST_DATABASE_URL).resolve_register_scope(
        [f"legacy-live.{secrets.token_urlsafe(8)}"]
    )
    assert scope is None
    del scoped
