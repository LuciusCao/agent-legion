"""Data migration (v57): sweep legacy plaintext node-config secrets into the vault.

The v19 node-CMS cutover and pre-vault UI writes could leave plaintext secret
values in ``workspaces.node_config_json`` (and every ``jobs.frozen_config_json``
freeze derived from it). The dispatch chain tolerates them as a compatibility
window, but VAULT-SECRET-001 requires plaintext never persist: this migration
encrypts each legacy value into the workspace vault under the deterministic
``node:<workflow>:<node>:<field>`` name and replaces it with a
``{"secret_ref": ...}`` marker. Without a master key the value is stripped
(and logged) instead — mirroring the external-connections migration (v34):
the operator re-enters the credential in the node config UI.

Secret fields resolve from the workspace's published Agent definitions
(``versioned_entities`` definition_json → config_schema properties with
``secret: true``). Field names are unique across a workspace's capabilities
in practice (the v19 ``token`` pattern is the only historical secret), so the
union applies per workspace; plain string configs (``api_url`` etc.) are
never swept.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


def _resolve_master_key() -> str | None:
    """Env-only master key read (migrations stay free of service imports)."""
    key = os.environ.get("AGENT_LEGION_VAULT_MASTER_KEY", "").strip()
    if key:
        return key
    key_file = os.environ.get("AGENT_LEGION_VAULT_MASTER_KEY_FILE", "").strip()
    if key_file:
        return Path(key_file).read_text(encoding="utf-8").strip()
    return None


def _decode(raw: Any) -> dict[str, Any]:
    try:
        loaded = json.loads(str(raw or ""))
    except (TypeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _workspace_secret_fields(conn: Any, workspace_id: str) -> set[str]:
    """Secret-marked config field names across the workspace's published Agents."""
    rows = conn.execute(
        """
        select definition_json from versioned_entities
        where entity_type = 'agent' and workspace_id = %s and status = 'published'
        """,
        (workspace_id,),
    ).fetchall()
    fields: set[str] = set()
    for row in rows:
        definition = _decode(row["definition_json"])
        schema = definition.get("config_schema")
        properties = schema.get("properties") if isinstance(schema, dict) else None
        if not isinstance(properties, dict):
            continue
        fields.update(
            str(name)
            for name, prop in properties.items()
            if isinstance(prop, dict) and prop.get("secret")
        )
    return fields


def _sweep_values(
    values: dict[str, Any],
    secret_fields: set[str],
    conn: Any,
    fernet: Fernet | None,
    workspace_id: str,
    ref_name_prefix: str,
) -> bool:
    """Rewrite one node's config dict in place; True when anything changed."""
    changed = False
    for field in secret_fields:
        if field not in values:
            continue
        value = values[field]
        if isinstance(value, dict):
            continue  # already a ref/marker shape
        if not (isinstance(value, str) and value.strip()):
            values.pop(field)
            changed = True
            continue
        name = f"{ref_name_prefix}:{field}"
        if fernet is not None:
            ciphertext = fernet.encrypt(value.encode("utf-8")).decode("utf-8")
            conn.execute(
                "insert into workspace_secrets(workspace_id, name, ciphertext)"
                " values (%s, %s, %s)"
                " on conflict(workspace_id, name)"
                " do update set ciphertext=excluded.ciphertext, updated_at=current_timestamp",
                (workspace_id, name, ciphertext),
            )
            values[field] = {"secret_ref": name}
        else:
            logger.warning(
                "node secret sweep: master key missing, plaintext %s for workspace %s"
                " is dropped; re-enter the credential in the node config UI",
                name,
                workspace_id,
            )
            values.pop(field)
        changed = True
    return changed


def _sweep_node_config(conn: Any, fernet: Fernet | None) -> None:
    """Sweep ``workspaces.node_config_json`` (workflow → node → field)."""
    workspaces = conn.execute(
        "select id, node_config_json from workspaces where node_config_json is not null"
    ).fetchall()
    for row in workspaces:
        workspace_id = str(row["id"])
        node_config = _decode(row["node_config_json"])
        if not node_config:
            continue
        fields = _workspace_secret_fields(conn, workspace_id)
        if not fields:
            continue
        changed = False
        for workflow_key, nodes in node_config.items():
            if not isinstance(nodes, dict):
                continue
            for node_key, values in nodes.items():
                if not isinstance(values, dict):
                    continue
                changed = (
                    _sweep_values(
                        values,
                        fields,
                        conn,
                        fernet,
                        workspace_id,
                        f"node:{workflow_key}:{node_key}",
                    )
                    or changed
                )
        if changed:
            conn.execute(
                "update workspaces set node_config_json=%s where id=%s",
                (json.dumps(node_config, ensure_ascii=False), workspace_id),
            )


def _sweep_frozen_configs(conn: Any, fernet: Fernet | None) -> None:
    """Sweep ``jobs.frozen_config_json`` (node → field).

    Freezes are per-job snapshots of the workspace's node config; the secret
    field set is resolved per job's workspace at sweep time. The ref name for
    a frozen value mirrors the workspace override it was frozen from, so a
    swept freeze and a swept override land on the same vault entry.
    """
    rows = conn.execute(
        """
        select j.id, j.workspace_id, j.workflow_key, j.frozen_config_json
        from jobs j
        where j.frozen_config_json is not null
        """
    ).fetchall()
    for row in rows:
        workspace_id = str(row["workspace_id"])
        workflow_key = str(row["workflow_key"] or "")
        frozen = _decode(row["frozen_config_json"])
        if not frozen:
            continue
        fields = _workspace_secret_fields(conn, workspace_id)
        if not fields:
            continue
        changed = False
        for node_key, values in frozen.items():
            if not isinstance(values, dict):
                continue
            changed = (
                _sweep_values(
                    values,
                    fields,
                    conn,
                    fernet,
                    workspace_id,
                    f"node:{workflow_key}:{node_key}",
                )
                or changed
            )
        if changed:
            conn.execute(
                "update jobs set frozen_config_json=%s where id=%s",
                (json.dumps(frozen, ensure_ascii=False), str(row["id"])),
            )


def migrate_node_secret_sweep(conn: Any) -> None:
    """Sweep every workspace's legacy plaintext node-config secrets (v57)."""
    master_key = _resolve_master_key()
    fernet = Fernet(master_key.encode("utf-8")) if master_key else None
    _sweep_node_config(conn, fernet)
    _sweep_frozen_configs(conn, fernet)
