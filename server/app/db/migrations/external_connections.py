"""Instance-level external connection for the CMS (schema v34).

The CMS integration moves off the env/node-config channels onto a single
admin-managed ``cms-internal`` connection:

- credentials are collected (first match wins): env ``CMS_TOKEN`` /
  ``AGENT_LEGION_CMS_TOKEN`` (``BASECMS_TOKEN`` alias) or the first workspace
  vault node token → ``static_bearer``; otherwise the env token_gen quartet
  (``CMS_APP_ID`` / ``CMS_NONCE`` / ``CMS_SECRET`` / ``CMS_TOKEN_URL``,
  ``BASECMS_*`` aliases) → ``cms_hmac``. Secrets are Fernet-encrypted into
  ``instance_secrets``; without a master key the connection is created
  without the secret field and the operator re-enters it in the admin UI.
- endpoint config (base_url / api_url / question_list_url) is collected from
  env ``CMS_BASE_URL`` and workspace node overrides;
- workspace ``node_config_json`` CMS keys are rewritten to ``connection``;
- frozen intake batch payloads gain ``connection`` on the CMS nodes so the
  queued backlog dispatches against the connection;
- the published ``code-default`` executor definition is re-published with the
  ``connection`` config_schema property (legacy token/env/url keys dropped),
  following the built-in definition upgrade pattern (v31 ASR precedent).

Idempotent: once ``cms-internal`` exists and no legacy keys remain, every
step matches nothing. Migrations stay free of service imports so later
edits cannot rewrite history.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

_CONNECTION_KEY = "cms-internal"
# (workflow_key, node_key) pairs that talk to the CMS.
_CMS_NODES = (
    ("question_comprehension_info", "fetch_questions"),
    ("video_knowledge", "download"),
)
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


def _env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _resolve_master_key() -> str | None:
    """Env-only master key read (migrations stay free of service imports)."""
    key = os.environ.get("AGENT_LEGION_VAULT_MASTER_KEY", "").strip()
    if key:
        return key
    key_file = os.environ.get("AGENT_LEGION_VAULT_MASTER_KEY_FILE", "").strip()
    if key_file:
        return Path(key_file).read_text(encoding="utf-8").strip()
    return None


def _decode(value: Any) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value or ""))
    except (TypeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


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


def _iter_cms_overrides(conn: Any):
    """Yield (workspace_id, values) for every workspace CMS node override."""
    rows = conn.execute("select id, node_config_json from workspaces order by id").fetchall()
    for row in rows:
        node_config = _decode(row["node_config_json"])
        for workflow_key, node_key in _CMS_NODES:
            workflow = node_config.get(workflow_key)
            values = workflow.get(node_key) if isinstance(workflow, dict) else None
            if isinstance(values, dict):
                yield str(row["id"]), values


def _collect_workspace_tokens(conn: Any, fernet: Fernet | None) -> list[str]:
    """All distinct workspace node-config tokens, ordered by workspace id."""
    tokens: list[str] = []
    for workspace_id, values in _iter_cms_overrides(conn):
        raw = values.get("token")
        token = ""
        if isinstance(raw, dict) and raw.get("secret_ref") and fernet is not None:
            row = conn.execute(
                "select ciphertext from workspace_secrets where workspace_id=%s and name=%s",
                (workspace_id, str(raw["secret_ref"])),
            ).fetchone()
            if row is not None:
                try:
                    token = fernet.decrypt(str(row["ciphertext"]).encode("utf-8")).decode("utf-8")
                except Exception:  # undecryptable entry: keep looking
                    logger.warning(
                        "external connections migration: cannot decrypt %s",
                        raw["secret_ref"],
                    )
        elif isinstance(raw, str) and raw.strip():
            token = raw.strip()
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def _create_connection(conn: Any, fernet: Fernet | None) -> bool:
    """Create the ``cms-internal`` connection from env/workspace sources."""
    exists = conn.execute(
        "select 1 from external_connections where key=%s", (_CONNECTION_KEY,)
    ).fetchone()
    if exists is not None:
        return True

    # Priority mirrors the retired runtime chain: a workspace-bound token won
    # over the env-level default, so workspace tokens are collected first.
    workspace_tokens = _collect_workspace_tokens(conn, fernet)
    if len(workspace_tokens) > 1:
        logger.warning(
            "external connections migration: %d distinct workspace CMS tokens "
            "collapse into the single %s connection; workspaces that need "
            "their own credential require separate connections",
            len(workspace_tokens),
            _CONNECTION_KEY,
        )
    token = (workspace_tokens[0] if workspace_tokens else "") or _env(
        "CMS_TOKEN", "BASECMS_TOKEN", "AGENT_LEGION_CMS_TOKEN"
    )
    base_url = _env("CMS_BASE_URL", "BASECMS_BASE_URL")
    api_url = ""
    question_list_url = ""
    for _, values in _iter_cms_overrides(conn):
        base_url = base_url or str(values.get("base_url") or "").strip()
        api_url = api_url or str(values.get("api_url") or "").strip()
        question_list_url = question_list_url or str(values.get("question_list_url") or "").strip()
    config: dict[str, Any] = {}
    if base_url:
        config["base_url"] = base_url
    if api_url:
        config["api_url"] = api_url
    if question_list_url:
        config["question_list_url"] = question_list_url

    if token:
        type_name = "static_bearer"
        marker = _store_instance_secret(conn, fernet, f"conn:{_CONNECTION_KEY}:token", token)
        if marker is not None:
            config["token"] = marker
    else:
        app_id = _env("CMS_APP_ID", "BASECMS_APP_ID")
        nonce = _env("CMS_NONCE", "BASECMS_NONCE")
        secret = _env("CMS_SECRET", "BASECMS_SECRET")
        token_url = _env("CMS_TOKEN_URL", "BASECMS_TOKEN_URL")
        if not all((app_id, nonce, secret, token_url)):
            logger.info(
                "external connections migration: no CMS credentials found, skipping "
                "connection creation"
            )
            return False
        type_name = "cms_hmac"
        config["app_id"] = app_id
        config["nonce"] = nonce
        config["token_url"] = token_url
        marker = _store_instance_secret(conn, fernet, f"conn:{_CONNECTION_KEY}:secret", secret)
        if marker is not None:
            config["secret"] = marker

    probe_url = api_url or (f"{base_url.rstrip('/')}/question/detail" if base_url else "")
    if probe_url:
        config["probe_url"] = probe_url
    conn.execute(
        "insert into external_connections(key, type, display_name, config_json)"
        " values (%s, %s, %s, %s) on conflict(key) do nothing",
        (
            _CONNECTION_KEY,
            type_name,
            "CMS（内部题库）",
            json.dumps(config, ensure_ascii=False),
        ),
    )
    logger.info(
        "external connections migration: created connection %s (type=%s)",
        _CONNECTION_KEY,
        type_name,
    )
    return True


def _rewrite_workspace_node_configs(conn: Any) -> None:
    rows = conn.execute("select id, node_config_json from workspaces").fetchall()
    for row in rows:
        node_config = _decode(row["node_config_json"])
        changed = False
        for workflow_key, node_key in _CMS_NODES:
            workflow = node_config.get(workflow_key)
            values = workflow.get(node_key) if isinstance(workflow, dict) else None
            if not isinstance(values, dict):
                continue
            for legacy in _LEGACY_NODE_KEYS:
                if values.pop(legacy, None) is not None:
                    changed = True
            if values.get("connection") != _CONNECTION_KEY:
                values["connection"] = _CONNECTION_KEY
                changed = True
        if changed:
            conn.execute(
                "update workspaces set node_config_json=%s where id=%s",
                (json.dumps(node_config, ensure_ascii=False), row["id"]),
            )


def _rewrite_batch_payloads(conn: Any) -> None:
    """Frozen intake configs of CMS nodes gain the connection reference.

    Legacy keys stay (the node ignores them once a connection is injected);
    the workspace vault entries they may reference are deliberately kept.
    """
    rows = conn.execute(
        "select id, source_payload_json from job_batches"
        " where workflow_key in ('question_comprehension_info', 'video_knowledge')"
    ).fetchall()
    for row in rows:
        payload = _decode(row["source_payload_json"])
        node_config = payload.get("node_config")
        if not isinstance(node_config, dict):
            continue
        changed = False
        for node_key in _BATCH_NODE_KEYS:
            values = node_config.get(node_key)
            if isinstance(values, dict) and values.get("connection") != _CONNECTION_KEY:
                values["connection"] = _CONNECTION_KEY
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
    """Collect CMS credentials/config into the cms-internal connection (v34)."""
    fernet_key = _resolve_master_key()
    fernet = Fernet(fernet_key.encode("utf-8")) if fernet_key else None
    if not _create_connection(conn, fernet):
        return
    _rewrite_workspace_node_configs(conn)
    _rewrite_batch_payloads(conn)
    _republish_executor_schema(conn)
