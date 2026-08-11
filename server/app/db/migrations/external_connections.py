"""Instance-level external connections for the CMS (schema v34).

The CMS integration moves off the env/node-config channels onto admin-managed
connections, one per distinct credential:

- each distinct workspace vault node token gets its own ``static_bearer``
  connection (``cms-internal``, ``cms-internal-2``, …, in workspace-id
  order), so no workspace ends up on another workspace's credential;
  token-less workspaces fall back to the env credential — ``CMS_TOKEN`` /
  ``AGENT_LEGION_CMS_TOKEN`` (``BASECMS_TOKEN`` alias) → ``static_bearer``,
  otherwise the env token_gen quartet (``CMS_APP_ID`` / ``CMS_NONCE`` /
  ``CMS_SECRET`` / ``CMS_TOKEN_URL``, ``BASECMS_*`` aliases) → ``cms_hmac``.
  Secrets are Fernet-encrypted into ``instance_secrets``; without a master
  key the connection is created without the secret field and the operator
  re-enters it in the admin UI. A workspace whose vault token cannot be
  decrypted is left untouched (its credential cannot be classified);
- endpoint config (base_url / api_url / question_list_url) comes from env
  ``CMS_BASE_URL`` and workspace node overrides, merged per group;
- workspace ``node_config_json`` CMS keys are rewritten to ``connection``
  pointing at the workspace's own credential group, and frozen intake batch
  payloads gain ``connection`` on the CMS nodes;
- the published ``code-default`` executor definition is re-published with the
  ``connection`` config_schema property (v31 ASR precedent).

Idempotent: once the connections exist and no legacy keys remain, every step
matches nothing. Migrations stay free of service imports so later edits
cannot rewrite history; the credential-grouping helpers live in the sibling
``external_connections_groups`` module (file-size budget split, equally
frozen).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

from server.app.db.migrations.external_connections_groups import (
    _CMS_NODES,
    _CONNECTION_KEY,
    _decode,
    _merge_endpoints,
    _plan_connections,
    _workspace_cms_state,
)

logger = logging.getLogger(__name__)

_LEGACY_NODE_KEYS = ("token", "env", "base_url", "api_url", "question_list_url", "knowledge_url")

# Frozen snapshot of the v34 factory property (migrations stay free of
# executor imports so later catalog edits cannot rewrite history).
_CONNECTION_PROPERTY: dict[str, Any] = {
    "type": "string",
    "default": _CONNECTION_KEY,
    "description": "外部服务连接 key（admin 全局设置「外部服务连接」中维护；出厂默认值，可被节点/workspace 覆盖）",
}
_LEGACY_SCHEMA_KEYS = ("token", "env", "base_url", "api_url", "question_list_url")
_CMS_CAPABILITIES = ("fetch_questions", "download_video")
_BATCH_NODE_KEYS = ("fetch_questions", "download")


def _resolve_master_key() -> str | None:
    """Env-only master key read (migrations stay free of service imports)."""
    key = os.environ.get("AGENT_LEGION_VAULT_MASTER_KEY", "").strip()
    if key:
        return key
    key_file = os.environ.get("AGENT_LEGION_VAULT_MASTER_KEY_FILE", "").strip()
    if key_file:
        return Path(key_file).read_text(encoding="utf-8").strip()
    return None


def _store_instance_secret(
    conn: Any, fernet: Fernet | None, name: str, plaintext: str
) -> dict[str, Any] | None:
    """Encrypt a credential into the instance vault; return the ref marker.

    Without a master key the secret cannot be stored: the connection row is
    created without the field and the operator re-enters it in the admin UI.
    """
    if fernet is None:
        logger.warning(
            "external connections migration: master key missing, secret %s must be "
            "re-entered in the admin UI",
            name,
        )
        return None
    ciphertext = fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")
    conn.execute(
        "insert into instance_secrets(name, ciphertext) values (%s, %s)"
        " on conflict(name) do update set ciphertext=excluded.ciphertext,"
        " updated_at=current_timestamp",
        (name, ciphertext),
    )
    return {"secret_ref": name}


def _insert_connection(conn: Any, fernet: Fernet | None, plan: dict[str, Any]) -> None:
    key, kind = str(plan["key"]), str(plan["kind"])
    endpoints = _merge_endpoints(plan)
    config: dict[str, Any] = {
        field: endpoints[field]
        for field in ("base_url", "api_url", "question_list_url")
        if endpoints[field]
    }
    if kind == "cms_hmac":
        config.update({k: plan[k] for k in ("app_id", "nonce", "token_url")})
    secret_field = "token" if kind == "static_bearer" else "secret"
    marker = _store_instance_secret(
        conn, fernet, f"conn:{key}:{secret_field}", str(plan[secret_field])
    )
    if marker is not None:
        config[secret_field] = marker
    base_url = endpoints["base_url"]
    probe_url = endpoints["api_url"] or (
        f"{base_url.rstrip('/')}/question/detail" if base_url else ""
    )
    if probe_url:
        config["probe_url"] = probe_url
    conn.execute(
        "insert into external_connections(key, type, display_name, config_json)"
        " values (%s, %s, %s, %s) on conflict(key) do nothing",
        (key, kind, "CMS（内部题库）", json.dumps(config, ensure_ascii=False)),
    )
    logger.info(
        "external connections migration: created connection %s (type=%s, workspaces=%d)",
        key,
        kind,
        len(plan["members"]),
    )


def _create_connections(conn: Any, fernet: Fernet | None) -> tuple[dict[str, str], set[str]]:
    """Create one connection per distinct credential.

    Returns (workspace_id → connection key binding, undecryptable workspace
    ids whose legacy node config must stay untouched).
    """
    entries, undecryptable = _workspace_cms_state(conn, fernet)
    plans, binding = _plan_connections(entries)
    if not plans:
        logger.info(
            "external connections migration: no CMS credentials found, skipping connection creation"
        )
    for plan in plans:
        _insert_connection(conn, fernet, plan)
    return binding, undecryptable


def _rewrite_workspace_node_configs(conn: Any, binding: dict[str, str], skipped: set[str]) -> None:
    rows = conn.execute("select id, node_config_json from workspaces").fetchall()
    for row in rows:
        workspace_id = str(row["id"])
        if workspace_id in skipped:
            # Credential unreadable: binding this workspace to any connection
            # would be a guess, so its legacy node config stays as-is.
            continue
        node_config = _decode(row["node_config_json"])
        connection_key = binding.get(workspace_id, _CONNECTION_KEY)
        changed = False
        for workflow_key, node_key in _CMS_NODES:
            workflow = node_config.get(workflow_key)
            values = workflow.get(node_key) if isinstance(workflow, dict) else None
            if not isinstance(values, dict):
                continue
            for legacy in _LEGACY_NODE_KEYS:
                if values.pop(legacy, None) is not None:
                    changed = True
            if values.get("connection") != connection_key:
                values["connection"] = connection_key
                changed = True
        if changed:
            conn.execute(
                "update workspaces set node_config_json=%s where id=%s",
                (json.dumps(node_config, ensure_ascii=False), row["id"]),
            )


def _rewrite_batch_payloads(conn: Any, binding: dict[str, str]) -> None:
    """Frozen intake configs of CMS nodes gain the connection reference.

    Legacy keys stay (the node ignores them once a connection is injected);
    the workspace vault entries they may reference are deliberately kept. Each
    batch binds to its workspace's own credential group when one exists.
    """
    rows = conn.execute(
        "select id, workspace_id, source_payload_json from job_batches"
        " where workflow_key in ('question_comprehension_info', 'video_knowledge')"
    ).fetchall()
    for row in rows:
        payload = _decode(row["source_payload_json"])
        node_config = payload.get("node_config")
        if not isinstance(node_config, dict):
            continue
        connection_key = binding.get(str(row["workspace_id"]), _CONNECTION_KEY)
        changed = False
        for node_key in _BATCH_NODE_KEYS:
            values = node_config.get(node_key)
            if isinstance(values, dict) and values.get("connection") != connection_key:
                values["connection"] = connection_key
                changed = True
        if changed:
            conn.execute(
                "update job_batches set source_payload_json=%s where id=%s",
                (json.dumps(payload, ensure_ascii=False), row["id"]),
            )


def _canonical_hash(definition: dict[str, Any]) -> tuple[str, str]:
    canonical = json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), canonical


def _republish_executor_schema(conn: Any) -> None:
    """Re-publish code-default with the connection config_schema (v34)."""
    rows = conn.execute(
        "select id, entity_key, definition_json from versioned_entities"
        " where entity_type='executor' and workspace_id is null and status='published'"
        " and entity_key='code-default'"
    ).fetchall()
    for row in rows:
        definition = json.loads(str(row["definition_json"]))
        capabilities = definition.get("capabilities")
        if not isinstance(capabilities, dict):
            continue
        changed = False
        for capability_key in _CMS_CAPABILITIES:
            capability = capabilities.get(capability_key)
            if not isinstance(capability, dict):
                continue
            schema = capability.get("config_schema")
            properties = schema.get("properties") if isinstance(schema, dict) else None
            if not isinstance(properties, dict):
                continue
            rewritten = {
                key: value for key, value in properties.items() if key not in _LEGACY_SCHEMA_KEYS
            }
            rewritten["connection"] = _CONNECTION_PROPERTY
            if rewritten == properties:
                continue
            capability["config_schema"] = {"type": "object", "properties": rewritten}
            changed = True
        if not changed:
            continue
        definition_hash, canonical = _canonical_hash(definition)
        latest = conn.execute(
            "select max(version) as v from versioned_entities"
            " where entity_type='executor' and workspace_id is null and entity_key=%s",
            (row["entity_key"],),
        ).fetchone()
        new_version = int(latest["v"]) + 1 if latest is not None else 1
        conn.execute(
            "update versioned_entities set status='archived' where id=%s",
            (row["id"],),
        )
        conn.execute(
            """
            insert into versioned_entities(
              id, entity_type, workspace_id, entity_key, version, status,
              definition_json, definition_hash, created_by, created_at, published_at
            ) values (%s, 'executor', null, %s, %s, 'published', %s, %s, 'system',
                      current_timestamp, current_timestamp)
            on conflict do nothing
            """,
            (
                f"executor:{row['entity_key']}:v{new_version}",
                row["entity_key"],
                new_version,
                canonical,
                definition_hash,
            ),
        )


def migrate_external_connections(conn: Any) -> None:
    """Collect CMS credentials/config into per-credential connections (v34)."""
    fernet_key = _resolve_master_key()
    fernet = Fernet(fernet_key.encode("utf-8")) if fernet_key else None
    binding, skipped = _create_connections(conn, fernet)
    # The rewrites run even when no connection could be created: legacy keys
    # would otherwise sit in node configs until the next restart and be
    # rejected by the new config_schema whitelist. The ``connection``
    # reference points at the default key (cms-internal) — also the
    # config_schema default — and starts resolving as soon as the operator
    # creates the connection in the admin UI.
    _rewrite_workspace_node_configs(conn, binding, skipped)
    _rewrite_batch_payloads(conn, binding)
    _republish_executor_schema(conn)
