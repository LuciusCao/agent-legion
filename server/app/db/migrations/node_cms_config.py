"""Data migration applied alongside the idempotent DDL replay (v19)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet


def _resolve_master_key() -> str | None:
    """Env-only master key read (migrations stay free of service imports)."""
    key = os.environ.get("AGENT_LEGION_VAULT_MASTER_KEY", "").strip()
    if key:
        return key
    key_file = os.environ.get("AGENT_LEGION_VAULT_MASTER_KEY_FILE", "").strip()
    if key_file:
        return Path(key_file).read_text(encoding="utf-8").strip()
    return None


# resource key → (workflow_key, node_key, node config field for the binding's api_url)
_NODE_BY_RESOURCE = {
    "question_detail": ("question_comprehension_info", "fetch_questions", "api_url"),
    "by_knowledge": ("question_comprehension_info", "fetch_questions", "question_list_url"),
    "knowledge_video": ("video_knowledge", "download", "api_url"),
}
# Non-secret binding config fields carried onto the node override (env stays
# global-only; api_url maps per resource above; token goes through the vault).
_CONFIG_KEYS = ("base_url", "bank_version", "country_id", "subject_id", "page_size")


def _decode(value: Any) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value or ""))
    except (TypeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _rename_vault_secret(conn: Any, workspace_id: str, old_name: str, new_name: str) -> None:
    """Rename a vault entry, letting a later rename win on name collision."""
    if old_name == new_name:
        return
    row = conn.execute(
        "select 1 from workspace_secrets where workspace_id=%s and name=%s",
        (workspace_id, old_name),
    ).fetchone()
    if row is None:
        return
    conn.execute(
        "delete from workspace_secrets where workspace_id=%s and name=%s",
        (workspace_id, new_name),
    )
    conn.execute(
        "update workspace_secrets set name=%s, updated_at=current_timestamp"
        " where workspace_id=%s and name=%s",
        (new_name, workspace_id, old_name),
    )


def _store_plaintext_token(
    conn: Any, fernet: Fernet | None, workspace_id: str, name: str, plaintext: str
) -> dict[str, Any] | str:
    """Encrypt the token into the vault when a master key is configured.

    Without a master key the plaintext carries over as-is: that preserves the
    spec D14 compatibility-window behavior the legacy binding already had,
    and the operator can re-enter the token in the node config UI once a key
    is configured.
    """
    if fernet is None:
        return plaintext
    ciphertext = fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")
    conn.execute(
        "insert into workspace_secrets(workspace_id, name, ciphertext) values (%s, %s, %s)"
        " on conflict(workspace_id, name)"
        " do update set ciphertext=excluded.ciphertext, updated_at=current_timestamp",
        (workspace_id, name, ciphertext),
    )
    return {"secret_ref": name}


def migrate_node_cms_config(conn: Any) -> None:
    """Move CMS resource bindings onto first-node config overrides (v19).

    Each workspace's ``resource_config.resources[*]`` binding maps onto the
    node config override of the corresponding first node (fetch_questions /
    download). Vault entries are renamed ``resource:<key>:token`` →
    ``node:<workflow>:<node>:token`` (same ciphertext); ``by_knowledge`` is
    processed after ``question_detail`` so its token wins the shared node.
    Existing node overrides win. ``resource_config`` is cleared; idempotent.
    """
    master_key = _resolve_master_key()
    fernet = Fernet(master_key.encode("utf-8")) if master_key else None
    workspaces = conn.execute("select id, resource_config_json, node_config_json from workspaces").fetchall()  # fmt: skip
    for row in workspaces:
        workspace_id = row["id"]
        resources = _decode(row["resource_config_json"]).get("resources")
        if not isinstance(resources, dict):
            continue
        node_config = _decode(row["node_config_json"])
        # Tokens set via the node config UI win over bindings; migration-set
        # tokens do not (by_knowledge wins the shared node).
        user_token_nodes = {
            (workflow_key, node_key)
            for workflow_key, nodes in node_config.items()
            if isinstance(nodes, dict)
            for node_key, values in nodes.items()
            if isinstance(values, dict) and "token" in values
        }
        for resource_key, (workflow_key, node_key, url_field) in _NODE_BY_RESOURCE.items():
            binding = resources.get(resource_key)
            if not isinstance(binding, dict) or binding.get("enabled") is False:
                continue
            config = binding.get("config")
            if not isinstance(config, dict):
                continue
            override = node_config.setdefault(workflow_key, {}).setdefault(node_key, {})
            for key in _CONFIG_KEYS:
                if key not in override and config.get(key) not in (None, ""):
                    override[key] = config[key]
            if url_field not in override and config.get("api_url"):
                override[url_field] = str(config["api_url"])
            if (workflow_key, node_key) in user_token_nodes:
                continue
            token_name = f"node:{workflow_key}:{node_key}:token"
            token = config.get("token")
            if isinstance(token, dict) and token.get("secret_ref"):
                _rename_vault_secret(conn, workspace_id, str(token["secret_ref"]), token_name)
                override["token"] = {"secret_ref": token_name}
            elif isinstance(token, str) and token.strip():
                override["token"] = _store_plaintext_token(
                    conn, fernet, workspace_id, token_name, token
                )
        conn.execute(
            "update workspaces set node_config_json=%s, resource_config_json='{}' where id=%s",
            (json.dumps(node_config, ensure_ascii=False), workspace_id),
        )
