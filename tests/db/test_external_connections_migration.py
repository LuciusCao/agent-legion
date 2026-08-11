"""Schema v34: CMS env/node-config 配置收编进实例级 cms-internal 连接。"""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from server.app.db.migrations import migrate_external_connections
from server.app.db.transaction import write_transaction
from tests.postgres_support import TEST_DATABASE_URL

_CMS_ENV = (
    "CMS_BASE_URL",
    "CMS_TOKEN",
    "CMS_APP_ID",
    "CMS_NONCE",
    "CMS_SECRET",
    "CMS_TOKEN_URL",
    "BASECMS_BASE_URL",
    "BASECMS_TOKEN",
    "BASECMS_APP_ID",
    "BASECMS_NONCE",
    "BASECMS_SECRET",
    "BASECMS_TOKEN_URL",
    "AGENT_LEGION_CMS_TOKEN",
)

_LEGACY_DEFINITION = {
    "kind": "code",
    "global_capacity": 16,
    "capabilities": {
        "fetch_questions": {
            "path": "workflow_nodes/question_intake.py",
            "sandbox_network": True,
            "config_schema": {
                "type": "object",
                "properties": {
                    "token": {"type": "string", "secret": True},
                    "env": {"type": "string", "default": "prod"},
                    "base_url": {"type": "string"},
                    "bank_version": {"type": "string", "default": "v5"},
                },
            },
        }
    },
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in _CMS_ENV:
        monkeypatch.delenv(name, raising=False)
    yield


def _insert_workspace(conn, workspace_id: str, node_config: dict) -> None:
    conn.execute(
        "insert into workspaces(id, name, node_config_json) values (%s, %s, %s)",
        (workspace_id, workspace_id, json.dumps(node_config)),
    )


def _seed_executor(conn) -> None:
    conn.execute(
        "delete from versioned_entities where entity_type='executor' and entity_key='code-default'"
    )
    conn.execute(
        "insert into versioned_entities("
        "id, entity_type, workspace_id, entity_key, version, status,"
        " definition_json, definition_hash, created_by)"
        " values ('executor:code-default:v1', 'executor', null, 'code-default', 1,"
        " 'published', %s, 'hash-v1', 'user:test')",
        (json.dumps(_LEGACY_DEFINITION),),
    )


def _connection_row(conn) -> dict | None:
    row = conn.execute(
        "select type, config_json from external_connections where key='cms-internal'"
    ).fetchone()
    return dict(row) if row else None


def _node_config(conn, workspace_id: str) -> dict:
    row = conn.execute(
        "select node_config_json from workspaces where id=%s", (workspace_id,)
    ).fetchone()
    return json.loads(row["node_config_json"])


def test_token_gen_env_becomes_cms_hmac_connection(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("CMS_APP_ID", "app")
    monkeypatch.setenv("CMS_NONCE", "nonce")
    monkeypatch.setenv("CMS_SECRET", "secret-value")
    monkeypatch.setenv("CMS_TOKEN_URL", "http://cms/token")
    monkeypatch.setenv("CMS_BASE_URL", "http://cms")
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_executor(conn)
        migrate_external_connections(conn)

        row = _connection_row(conn)
        assert row is not None
        assert row["type"] == "cms_hmac"
        config = json.loads(row["config_json"])
        assert config["app_id"] == "app"
        assert config["base_url"] == "http://cms"
        assert config["secret"] == {"secret_ref": "conn:cms-internal:secret"}
        secret_row = conn.execute(
            "select ciphertext from instance_secrets where name='conn:cms-internal:secret'"
        ).fetchone()
        assert secret_row is not None

        # Executor re-published with the connection property, legacy keys dropped.
        published = conn.execute(
            "select definition_json from versioned_entities"
            " where entity_key='code-default' and status='published'"
        ).fetchone()
        properties = json.loads(published["definition_json"])["capabilities"]["fetch_questions"][
            "config_schema"
        ]["properties"]
        assert properties["connection"]["default"] == "cms-internal"
        assert "token" not in properties
        assert properties["bank_version"]["default"] == "v5"


def test_workspace_vault_token_becomes_static_bearer(monkeypatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", key)
    ciphertext = Fernet(key.encode()).encrypt(b"workspace-token").decode()
    with write_transaction(TEST_DATABASE_URL) as conn:
        _insert_workspace(
            conn,
            "ws-mig-vault",
            {
                "question_comprehension_info": {
                    "fetch_questions": {
                        "token": {
                            "secret_ref": "node:question_comprehension_info:fetch_questions:token"
                        },
                        "base_url": "http://cms-ws",
                        "bank_version": "v5",
                    }
                }
            },
        )
        conn.execute(
            "insert into workspace_secrets(workspace_id, name, ciphertext) values (%s, %s, %s)",
            ("ws-mig-vault", "node:question_comprehension_info:fetch_questions:token", ciphertext),
        )
        migrate_external_connections(conn)

        row = _connection_row(conn)
        assert row is not None and row["type"] == "static_bearer"
        config = json.loads(row["config_json"])
        assert config["token"] == {"secret_ref": "conn:cms-internal:token"}
        assert config["base_url"] == "http://cms-ws"
        # The migrated token decrypts to the original workspace token.
        secret_row = conn.execute(
            "select ciphertext from instance_secrets where name='conn:cms-internal:token'"
        ).fetchone()
        assert Fernet(key.encode()).decrypt(secret_row["ciphertext"].encode()) == b"workspace-token"

        # Workspace node override rewritten to the connection reference.
        values = _node_config(conn, "ws-mig-vault")["question_comprehension_info"][
            "fetch_questions"
        ]
        assert values == {"bank_version": "v5", "connection": "cms-internal"}


def test_batch_payloads_gain_connection(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("CMS_TOKEN", "env-token")
    with write_transaction(TEST_DATABASE_URL) as conn:
        _insert_workspace(conn, "ws-mig-batch", {})
        conn.execute(
            "insert into job_batches(id, workspace_id, workflow_key, source_kind,"
            " source_payload_json) values ('b1', 'ws-mig-batch', 'question_comprehension_info',"
            " 'question_ids', %s)",
            (json.dumps({"node_config": {"fetch_questions": {"env": "prod"}}}),),
        )
        migrate_external_connections(conn)

        row = conn.execute("select source_payload_json from job_batches where id='b1'").fetchone()
        values = json.loads(row["source_payload_json"])["node_config"]["fetch_questions"]
        assert values["connection"] == "cms-internal"


def test_no_credentials_still_rewrites_legacy_keys() -> None:
    """No credentials anywhere: no connection is created, but the rewrites
    still run — legacy keys would otherwise be rejected by the new
    config_schema whitelist until the next restart. The connection reference
    points at the default key and resolves once the operator creates it."""
    with write_transaction(TEST_DATABASE_URL) as conn:
        _insert_workspace(
            conn,
            "ws-mig-none",
            {
                "question_comprehension_info": {
                    "fetch_questions": {"base_url": "http://cms-ws", "bank_version": "v5"}
                }
            },
        )
        migrate_external_connections(conn)
        assert _connection_row(conn) is None
        values = _node_config(conn, "ws-mig-none")["question_comprehension_info"]["fetch_questions"]
        assert values == {"bank_version": "v5", "connection": "cms-internal"}


def test_distinct_workspace_tokens_get_separate_connections(monkeypatch) -> None:
    """Two workspaces with different CMS tokens must not collapse into one
    connection: each gets its own (deterministic key order) and its node
    config binds to its own credential."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", key)
    fernet = Fernet(key.encode())

    def _ws_node_config(token: str, base_url: str) -> dict:
        return {
            "question_comprehension_info": {
                "fetch_questions": {
                    "token": {
                        "secret_ref": "node:question_comprehension_info:fetch_questions:token"
                    },
                    "base_url": base_url,
                }
            }
        }

    with write_transaction(TEST_DATABASE_URL) as conn:
        for workspace_id, token, base_url in (
            ("ws-mig-a", "token-a", "http://cms-a"),
            ("ws-mig-b", "token-b", "http://cms-b"),
        ):
            _insert_workspace(conn, workspace_id, _ws_node_config(token, base_url))
            conn.execute(
                "insert into workspace_secrets(workspace_id, name, ciphertext) values (%s, %s, %s)",
                (
                    workspace_id,
                    "node:question_comprehension_info:fetch_questions:token",
                    fernet.encrypt(token.encode()).decode(),
                ),
            )
        migrate_external_connections(conn)

        rows = conn.execute(
            "select key, type, config_json from external_connections order by key"
        ).fetchall()
        assert [row["key"] for row in rows] == ["cms-internal", "cms-internal-2"]
        first = json.loads(rows[0]["config_json"])
        second = json.loads(rows[1]["config_json"])
        assert rows[0]["type"] == rows[1]["type"] == "static_bearer"
        assert first["token"] == {"secret_ref": "conn:cms-internal:token"}
        assert second["token"] == {"secret_ref": "conn:cms-internal-2:token"}
        # Each connection carries its own group's endpoint.
        assert first["base_url"] == "http://cms-a"
        assert second["base_url"] == "http://cms-b"
        # Each stored credential decrypts to its own workspace's token.
        for name, expected in (
            ("conn:cms-internal:token", b"token-a"),
            ("conn:cms-internal-2:token", b"token-b"),
        ):
            secret_row = conn.execute(
                "select ciphertext from instance_secrets where name=%s", (name,)
            ).fetchone()
            assert fernet.decrypt(secret_row["ciphertext"].encode()) == expected
        # Each workspace binds to its own connection.
        assert _node_config(conn, "ws-mig-a")["question_comprehension_info"]["fetch_questions"] == {
            "connection": "cms-internal"
        }
        assert _node_config(conn, "ws-mig-b")["question_comprehension_info"]["fetch_questions"] == {
            "connection": "cms-internal-2"
        }


def test_shared_workspace_token_gets_one_connection(monkeypatch) -> None:
    """Workspaces sharing the same token share one connection."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", key)
    fernet = Fernet(key.encode())
    with write_transaction(TEST_DATABASE_URL) as conn:
        for workspace_id in ("ws-mig-s1", "ws-mig-s2"):
            _insert_workspace(
                conn,
                workspace_id,
                {
                    "question_comprehension_info": {
                        "fetch_questions": {
                            "token": {
                                "secret_ref": "node:question_comprehension_info:fetch_questions:token"
                            }
                        }
                    }
                },
            )
            conn.execute(
                "insert into workspace_secrets(workspace_id, name, ciphertext) values (%s, %s, %s)",
                (
                    workspace_id,
                    "node:question_comprehension_info:fetch_questions:token",
                    fernet.encrypt(b"shared-token").decode(),
                ),
            )
        migrate_external_connections(conn)

        rows = conn.execute("select key from external_connections order by key").fetchall()
        assert [row["key"] for row in rows] == ["cms-internal"]
        for workspace_id in ("ws-mig-s1", "ws-mig-s2"):
            values = _node_config(conn, workspace_id)["question_comprehension_info"][
                "fetch_questions"
            ]
            assert values == {"connection": "cms-internal"}


def test_undecryptable_workspace_token_left_untouched(monkeypatch) -> None:
    """A workspace whose vault token cannot be decrypted keeps its legacy
    node config: binding it to another workspace's connection would leak the
    wrong credential."""
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", Fernet.generate_key().decode())
    other_key = Fernet.generate_key().decode()
    with write_transaction(TEST_DATABASE_URL) as conn:
        _insert_workspace(
            conn,
            "ws-mig-bad",
            {
                "question_comprehension_info": {
                    "fetch_questions": {
                        "token": {
                            "secret_ref": "node:question_comprehension_info:fetch_questions:token"
                        },
                        "bank_version": "v5",
                    }
                }
            },
        )
        # Ciphertext encrypted with a different key: undecryptable here.
        conn.execute(
            "insert into workspace_secrets(workspace_id, name, ciphertext) values (%s, %s, %s)",
            (
                "ws-mig-bad",
                "node:question_comprehension_info:fetch_questions:token",
                Fernet(other_key.encode()).encrypt(b"alien-token").decode(),
            ),
        )
        migrate_external_connections(conn)

        assert _connection_row(conn) is None
        values = _node_config(conn, "ws-mig-bad")["question_comprehension_info"]["fetch_questions"]
        assert values["token"] == {
            "secret_ref": "node:question_comprehension_info:fetch_questions:token"
        }
        assert "connection" not in values


def test_idempotent_replay(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("CMS_TOKEN", "env-token")
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_executor(conn)
        migrate_external_connections(conn)
        migrate_external_connections(conn)

        rows = conn.execute(
            "select version, status from versioned_entities"
            " where entity_key='code-default' order by version"
        ).fetchall()
        assert [(row["version"], row["status"]) for row in rows] == [
            (1, "archived"),
            (2, "published"),
        ]
