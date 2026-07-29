"""Schema v19: CMS resource bindings fold into first-node config overrides."""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet

from server.app.db.migrations import migrate_node_cms_config
from server.app.db.transaction import write_transaction
from tests.postgres_support import TEST_DATABASE_URL

FETCH_TOKEN = "node:question_comprehension_info:fetch_questions:token"
DOWNLOAD_TOKEN = "node:video_knowledge:download:token"


def _insert_workspace(
    conn: Any,
    workspace_id: str,
    resources: dict[str, Any],
    node_config: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "insert into workspaces(id, name, resource_config_json, node_config_json)"
        " values (?, ?, ?, ?)",
        (
            workspace_id,
            workspace_id,
            json.dumps({"resources": resources}),
            json.dumps(node_config or {}),
        ),
    )


def _add_secret(conn: Any, workspace_id: str, name: str, ciphertext: str) -> None:
    conn.execute(
        "insert into workspace_secrets(workspace_id, name, ciphertext) values (?, ?, ?)",
        (workspace_id, name, ciphertext),
    )


def _workspace_config(conn: Any, workspace_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    row = conn.execute(
        "select resource_config_json, node_config_json from workspaces where id=?",
        (workspace_id,),
    ).fetchone()
    return json.loads(row["resource_config_json"]), json.loads(row["node_config_json"])


def _secrets(conn: Any, workspace_id: str) -> dict[str, str]:
    rows = conn.execute(
        "select name, ciphertext from workspace_secrets where workspace_id=?",
        (workspace_id,),
    ).fetchall()
    return {row["name"]: row["ciphertext"] for row in rows}


def test_migration_maps_bindings_to_node_overrides() -> None:
    resources = {
        "question_detail": {
            "enabled": True,
            "config": {
                "api_url": "http://cms.example/detail",
                "bank_version": "v6",
                "country_id": "3",
                "subject_id": "4",
                "token": {"secret_ref": "resource:question_detail:token"},
            },
        },
        "by_knowledge": {
            "enabled": True,
            "config": {
                "api_url": "http://cms.example/list",
                "page_size": 100,
                "token": {"secret_ref": "resource:by_knowledge:token"},
            },
        },
        "knowledge_video": {
            "enabled": True,
            "config": {
                "api_url": "http://cms.example/knowledge",
                "token": {"secret_ref": "resource:knowledge_video:token"},
            },
        },
    }
    with write_transaction(TEST_DATABASE_URL) as conn:
        _insert_workspace(conn, "node-cms-mig-ws1", resources)
        for key in ("question_detail", "by_knowledge", "knowledge_video"):
            _add_secret(conn, "node-cms-mig-ws1", f"resource:{key}:token", f"CT-{key}")
        migrate_node_cms_config(conn)
        resource_config, node_config = _workspace_config(conn, "node-cms-mig-ws1")

    fetch = node_config["question_comprehension_info"]["fetch_questions"]
    assert fetch["api_url"] == "http://cms.example/detail"
    assert fetch["question_list_url"] == "http://cms.example/list"
    # Non-secret selector keys carry over; page_size comes from by_knowledge.
    assert fetch["bank_version"] == "v6"
    assert fetch["country_id"] == "3"
    assert fetch["subject_id"] == "4"
    assert fetch["page_size"] == 100
    assert fetch["token"] == {"secret_ref": FETCH_TOKEN}
    download = node_config["video_knowledge"]["download"]
    assert download["api_url"] == "http://cms.example/knowledge"
    assert download["token"] == {"secret_ref": DOWNLOAD_TOKEN}
    assert resource_config == {}


def test_migration_renames_vault_entries_keeping_ciphertext() -> None:
    resources = {
        "question_detail": {
            "enabled": True,
            "config": {
                "api_url": "http://cms.example/detail",
                "token": {"secret_ref": "resource:question_detail:token"},
            },
        },
        "knowledge_video": {
            "enabled": True,
            "config": {
                "api_url": "http://cms.example/knowledge",
                "token": {"secret_ref": "resource:knowledge_video:token"},
            },
        },
    }
    with write_transaction(TEST_DATABASE_URL) as conn:
        _insert_workspace(conn, "node-cms-mig-ws2", resources)
        _add_secret(conn, "node-cms-mig-ws2", "resource:question_detail:token", "CT-detail")
        _add_secret(conn, "node-cms-mig-ws2", "resource:knowledge_video:token", "CT-video")
        migrate_node_cms_config(conn)
        secrets = _secrets(conn, "node-cms-mig-ws2")

    # The ciphertext is untouched: entries decrypt with the same master key.
    assert secrets == {FETCH_TOKEN: "CT-detail", DOWNLOAD_TOKEN: "CT-video"}


def test_migration_skips_disabled_bindings() -> None:
    resources = {
        "question_detail": {
            "enabled": False,
            "config": {"api_url": "http://cms.example/detail"},
        },
        "by_knowledge": {
            "enabled": True,
            "config": {"api_url": "http://cms.example/list"},
        },
    }
    with write_transaction(TEST_DATABASE_URL) as conn:
        _insert_workspace(conn, "node-cms-mig-ws3", resources)
        migrate_node_cms_config(conn)
        resource_config, node_config = _workspace_config(conn, "node-cms-mig-ws3")

    fetch = node_config["question_comprehension_info"]["fetch_questions"]
    assert "api_url" not in fetch
    assert fetch["question_list_url"] == "http://cms.example/list"
    assert resource_config == {}


def test_migration_by_knowledge_token_wins_the_shared_node() -> None:
    resources = {
        "question_detail": {
            "enabled": True,
            "config": {"token": {"secret_ref": "resource:question_detail:token"}},
        },
        "by_knowledge": {
            "enabled": True,
            "config": {"token": {"secret_ref": "resource:by_knowledge:token"}},
        },
    }
    with write_transaction(TEST_DATABASE_URL) as conn:
        _insert_workspace(conn, "node-cms-mig-ws3b", resources)
        _add_secret(conn, "node-cms-mig-ws3b", "resource:question_detail:token", "CT-detail")
        _add_secret(conn, "node-cms-mig-ws3b", "resource:by_knowledge:token", "CT-knowledge")
        migrate_node_cms_config(conn)
        _, node_config = _workspace_config(conn, "node-cms-mig-ws3b")
        secrets = _secrets(conn, "node-cms-mig-ws3b")

    fetch = node_config["question_comprehension_info"]["fetch_questions"]
    assert fetch["token"] == {"secret_ref": FETCH_TOKEN}
    # by_knowledge is processed after question_detail and wins the shared
    # node: its ciphertext replaces the detail one, and no resource:* entry
    # is left orphaned.
    assert secrets == {FETCH_TOKEN: "CT-knowledge"}


def test_migration_user_node_token_wins_over_bindings() -> None:
    resources = {
        "question_detail": {
            "enabled": True,
            "config": {"token": {"secret_ref": "resource:question_detail:token"}},
        },
        "by_knowledge": {
            "enabled": True,
            "config": {"token": {"secret_ref": "resource:by_knowledge:token"}},
        },
    }
    existing = {
        "question_comprehension_info": {
            "fetch_questions": {"token": {"secret_ref": "node:custom:token"}}
        }
    }
    with write_transaction(TEST_DATABASE_URL) as conn:
        _insert_workspace(conn, "node-cms-mig-ws3c", resources, node_config=existing)
        _add_secret(conn, "node-cms-mig-ws3c", "resource:question_detail:token", "CT-detail")
        migrate_node_cms_config(conn)
        _, node_config = _workspace_config(conn, "node-cms-mig-ws3c")
        secrets = _secrets(conn, "node-cms-mig-ws3c")

    fetch = node_config["question_comprehension_info"]["fetch_questions"]
    # A token already set via the node config UI is left untouched and the
    # binding vault entries are not renamed.
    assert fetch["token"] == {"secret_ref": "node:custom:token"}
    assert secrets == {"resource:question_detail:token": "CT-detail"}


def test_migration_existing_node_override_wins_over_binding() -> None:
    resources = {
        "question_detail": {
            "enabled": True,
            "config": {
                "api_url": "http://binding.example/detail",
                "bank_version": "v6",
                "token": {"secret_ref": "resource:question_detail:token"},
            },
        },
    }
    existing = {
        "question_comprehension_info": {
            "fetch_questions": {"api_url": "http://override.example/detail"}
        }
    }
    with write_transaction(TEST_DATABASE_URL) as conn:
        _insert_workspace(conn, "node-cms-mig-ws4", resources, node_config=existing)
        _add_secret(conn, "node-cms-mig-ws4", "resource:question_detail:token", "CT-detail")
        migrate_node_cms_config(conn)
        _, node_config = _workspace_config(conn, "node-cms-mig-ws4")
        secrets = _secrets(conn, "node-cms-mig-ws4")

    fetch = node_config["question_comprehension_info"]["fetch_questions"]
    # The pre-existing override keeps its values; missing keys are filled in.
    assert fetch["api_url"] == "http://override.example/detail"
    assert fetch["bank_version"] == "v6"
    assert fetch["token"] == {"secret_ref": FETCH_TOKEN}
    assert secrets == {FETCH_TOKEN: "CT-detail"}


def test_migration_encrypts_plaintext_token_with_master_key(monkeypatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", key)
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    resources = {
        "question_detail": {
            "enabled": True,
            "config": {"api_url": "http://cms.example/detail", "token": "plain-token"},
        },
    }
    with write_transaction(TEST_DATABASE_URL) as conn:
        _insert_workspace(conn, "node-cms-mig-ws5", resources)
        migrate_node_cms_config(conn)
        _, node_config = _workspace_config(conn, "node-cms-mig-ws5")
        secrets = _secrets(conn, "node-cms-mig-ws5")

    fetch = node_config["question_comprehension_info"]["fetch_questions"]
    assert fetch["token"] == {"secret_ref": FETCH_TOKEN}
    ciphertext = secrets[FETCH_TOKEN]
    assert ciphertext != "plain-token"
    assert "plain-token" not in ciphertext
    assert Fernet(key.encode()).decrypt(ciphertext.encode()).decode() == "plain-token"


def test_migration_keeps_plaintext_token_without_master_key(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY", raising=False)
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    resources = {
        "question_detail": {
            "enabled": True,
            "config": {"api_url": "http://cms.example/detail", "token": "plain-token"},
        },
    }
    with write_transaction(TEST_DATABASE_URL) as conn:
        _insert_workspace(conn, "node-cms-mig-ws6", resources)
        migrate_node_cms_config(conn)
        _, node_config = _workspace_config(conn, "node-cms-mig-ws6")
        secrets = _secrets(conn, "node-cms-mig-ws6")

    fetch = node_config["question_comprehension_info"]["fetch_questions"]
    # No master key: the plaintext carries over as-is for the operator to
    # re-enter once a key is configured.
    assert fetch["token"] == "plain-token"
    assert secrets == {}


def test_migration_is_idempotent() -> None:
    resources = {
        "question_detail": {
            "enabled": True,
            "config": {
                "api_url": "http://cms.example/detail",
                "token": {"secret_ref": "resource:question_detail:token"},
            },
        },
    }
    with write_transaction(TEST_DATABASE_URL) as conn:
        _insert_workspace(conn, "node-cms-mig-ws7", resources)
        _add_secret(conn, "node-cms-mig-ws7", "resource:question_detail:token", "CT-detail")
        migrate_node_cms_config(conn)
        first = _workspace_config(conn, "node-cms-mig-ws7")
        first_secrets = _secrets(conn, "node-cms-mig-ws7")
        migrate_node_cms_config(conn)
        second = _workspace_config(conn, "node-cms-mig-ws7")
        second_secrets = _secrets(conn, "node-cms-mig-ws7")

    assert first == second
    assert first_secrets == second_secrets
