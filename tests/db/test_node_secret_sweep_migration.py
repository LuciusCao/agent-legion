"""Schema v57: legacy plaintext node-config secret sweep (VAULT-SECRET-001).

The sweep encrypts legacy plaintext secret values in
``workspaces.node_config_json`` and ``jobs.frozen_config_json`` into the
workspace vault, replacing them with ``{"secret_ref": ...}`` markers. Without
a master key the values are dropped with a warning (the v34
external-connections migration precedent: the operator re-enters the token).
"""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from server.app.db.migrations.node_secret_sweep import migrate_node_secret_sweep
from server.app.db.schema import SCHEMA_VERSION
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL

SECRET_FIELD = "token"
SECRET_NAME = "node:wf_demo:fetch:token"


def test_schema_v57_recorded() -> None:
    assert SCHEMA_VERSION == 57
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select name from schema_migrations where version=%s", (SCHEMA_VERSION,)
        ).fetchone()
    assert row is not None
    assert row["name"] == "node_secret_sweep"


@pytest.fixture
def master_key(monkeypatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", key)
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    return key


def _seed(conn, workspace_id: str, node_config: dict, frozen_config: dict | None) -> None:
    conn.execute(
        """
        insert into workspaces(id, name, default_workflow_key, node_config_json)
        values (%s, %s, 'wf_demo', %s)
        on conflict (id) do update set node_config_json=excluded.node_config_json
        """,
        (workspace_id, workspace_id, json.dumps(node_config)),
    )
    conn.execute(
        """
        insert into versioned_entities(
          id, entity_type, workspace_id, entity_key, version, status,
          definition_json, definition_hash, created_by
        )
        values (%s, 'agent', %s, 'fetch-agent', 1, 'published', %s, %s, 'migration-test')
        on conflict do nothing
        """,
        (
            f"ve-{workspace_id}",
            workspace_id,
            json.dumps(
                {
                    "capability": "fetch_questions",
                    "runtime": "velites",
                    "skill": "demo",
                    "config_schema": {
                        "type": "object",
                        "properties": {
                            "api_url": {"type": "string"},
                            SECRET_FIELD: {"type": "string", "secret": True},
                        },
                    },
                }
            ),
            f"hash-{workspace_id}",
        ),
    )
    if frozen_config is not None:
        conn.execute(
            """
            insert into jobs(
              id, workspace_id, workflow_key, source_type, source_id, frozen_config_json
            )
            values (%s, %s, 'wf_demo', 'question', 'q1', %s)
            on conflict (id) do update
              set frozen_config_json=excluded.frozen_config_json
            """,
            (f"job-{workspace_id}", workspace_id, json.dumps(frozen_config)),
        )


def _node_config(conn, workspace_id: str) -> dict:
    row = conn.execute(
        "select node_config_json from workspaces where id=%s", (workspace_id,)
    ).fetchone()
    return json.loads(str(row["node_config_json"]))


def _vault_ciphertext(conn, workspace_id: str, name: str) -> str | None:
    row = conn.execute(
        "select ciphertext from workspace_secrets where workspace_id=%s and name=%s",
        (workspace_id, name),
    ).fetchone()
    return str(row["ciphertext"]) if row is not None else None


@pytest.mark.fresh_schema
def test_sweep_vaults_plaintext_with_master_key(master_key) -> None:
    """v56 → v57 replay: workspace overrides and job freezes are vaulted."""
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version=%s", (SCHEMA_VERSION,))
        _seed(
            conn,
            "ws-sweep",
            {"wf_demo": {"fetch": {"api_url": "http://x", SECRET_FIELD: "plain-token"}}},
            {"fetch": {"api_url": "http://x", SECRET_FIELD: "plain-token"}},
        )
        migrate_node_secret_sweep(conn)

    with read_connection(TEST_DATABASE_URL) as conn:
        node_config = _node_config(conn, "ws-sweep")
        frozen_row = conn.execute(
            "select frozen_config_json from jobs where id='job-ws-sweep'"
        ).fetchone()
        ciphertext = _vault_ciphertext(conn, "ws-sweep", SECRET_NAME)

    assert node_config["wf_demo"]["fetch"][SECRET_FIELD] == {"secret_ref": SECRET_NAME}
    assert node_config["wf_demo"]["fetch"]["api_url"] == "http://x"
    frozen = json.loads(str(frozen_row["frozen_config_json"]))
    assert frozen["fetch"][SECRET_FIELD] == {"secret_ref": SECRET_NAME}
    assert ciphertext is not None
    assert Fernet(master_key.encode()).decrypt(ciphertext.encode()).decode() == "plain-token"


@pytest.mark.fresh_schema
def test_sweep_without_master_key_drops_plaintext(monkeypatch, caplog) -> None:
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY", raising=False)
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version=%s", (SCHEMA_VERSION,))
        _seed(
            conn,
            "ws-nokey",
            {"wf_demo": {"fetch": {SECRET_FIELD: "plain-token", "unused": "keep-me"}}},
            None,
        )
        with caplog.at_level("WARNING"):
            migrate_node_secret_sweep(conn)

    with read_connection(TEST_DATABASE_URL) as conn:
        node_config = _node_config(conn, "ws-nokey")
        ciphertext = _vault_ciphertext(conn, "ws-nokey", SECRET_NAME)

    assert SECRET_FIELD not in node_config["wf_demo"]["fetch"]
    assert node_config["wf_demo"]["fetch"]["unused"] == "keep-me"
    assert ciphertext is None
    assert any("master key missing" in record.message for record in caplog.records)


@pytest.mark.fresh_schema
def test_sweep_is_idempotent_and_skips_ref_shapes(master_key) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version=%s", (SCHEMA_VERSION,))
        _seed(
            conn,
            "ws-idem",
            {
                "wf_demo": {
                    "fetch": {
                        SECRET_FIELD: {"secret_ref": "node:wf_demo:fetch:token"},
                        "api_url": "http://x",
                    }
                }
            },
            None,
        )
        migrate_node_secret_sweep(conn)
        migrate_node_secret_sweep(conn)

    with read_connection(TEST_DATABASE_URL) as conn:
        node_config = _node_config(conn, "ws-idem")

    assert node_config["wf_demo"]["fetch"][SECRET_FIELD] == {"secret_ref": SECRET_NAME}
