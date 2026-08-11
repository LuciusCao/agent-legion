"""Credential grouping helpers for the v34 external-connections migration.

Split out of ``external_connections.py`` only to respect the file-size
budget; both modules are one frozen migration (schema v34) — editing this
file later rewrites migration history exactly as editing the migration
itself would.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

_CONNECTION_KEY = "cms-internal"
# (workflow_key, node_key) pairs that talk to the CMS.
_CMS_NODES = (
    ("question_comprehension_info", "fetch_questions"),
    ("video_knowledge", "download"),
)
_ENDPOINT_KEYS = ("base_url", "api_url", "question_list_url")


def _env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _decode(value: Any) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value or ""))
    except (TypeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


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


def _decrypt_workspace_token(conn: Any, fernet: Fernet | None, workspace_id: str, name: str) -> str:
    """Decrypted workspace vault token; "" (with a warning) when unreadable."""
    if fernet is not None:
        row = conn.execute(
            "select ciphertext from workspace_secrets where workspace_id=%s and name=%s",
            (workspace_id, name),
        ).fetchone()
        if row is not None:
            try:
                return fernet.decrypt(str(row["ciphertext"]).encode("utf-8")).decode("utf-8")
            except Exception:  # undecryptable entry
                pass
    logger.warning(
        "external connections migration: cannot decrypt %s (workspace %s);"
        " leaving its legacy node config untouched",
        name,
        workspace_id,
    )
    return ""


def _workspace_cms_state(
    conn: Any, fernet: Fernet | None
) -> tuple[list[tuple[str, str, dict[str, str]]], set[str]]:
    """(workspace_id, token, endpoints) per workspace + undecryptable ids."""
    # Undecryptable vault tokens cannot be classified: the rewrite step
    # leaves those workspaces untouched rather than binding them to another
    # workspace's connection (cross-tenant risk).
    entries: list[tuple[str, str, dict[str, str]]] = []
    undecryptable: set[str] = set()
    for workspace_id, values in _iter_cms_overrides(conn):
        raw = values.get("token")
        token = raw.strip() if isinstance(raw, str) else ""
        if isinstance(raw, dict) and raw.get("secret_ref"):
            token = _decrypt_workspace_token(conn, fernet, workspace_id, str(raw["secret_ref"]))
            if not token:
                undecryptable.add(workspace_id)
        endpoints = {field: str(values.get(field) or "").strip() for field in _ENDPOINT_KEYS}
        entries.append((workspace_id, token, endpoints))
    return entries, undecryptable


def _plan_connections(
    entries: list[tuple[str, str, dict[str, str]]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Per-credential connection plans + workspace_id → connection key binding."""
    # Distinct workspace tokens get cms-internal, cms-internal-2, … in
    # first-appearance (workspace id) order; collapsing them would hand one
    # workspace's credential to another. The env credential (static token,
    # else the token_gen quartet) is the fallback group for token-less
    # workspaces.
    plans: list[dict[str, Any]] = []
    by_token: dict[str, dict[str, Any]] = {}
    for _, token, _ in entries:
        if token and token not in by_token:
            by_token[token] = {"kind": "static_bearer", "token": token, "members": []}
            plans.append(by_token[token])
    env_base_url = _env("CMS_BASE_URL", "BASECMS_BASE_URL")
    env_token = _env("CMS_TOKEN", "BASECMS_TOKEN", "AGENT_LEGION_CMS_TOKEN")
    fallback: dict[str, Any] | None = by_token.get(env_token) if env_token else None
    if env_token and fallback is None:
        fallback = {"kind": "static_bearer", "token": env_token, "members": []}
        plans.append(fallback)
    if fallback is None and not env_token:
        quartet = {
            key.lower()[4:]: _env(key, f"BASE{key}")
            for key in ("CMS_APP_ID", "CMS_NONCE", "CMS_SECRET", "CMS_TOKEN_URL")
        }
        if all(quartet.values()):
            fallback = {"kind": "cms_hmac", "members": [], **quartet}
            plans.append(fallback)
    if fallback is None and plans:
        # No env credential: token-less workspaces previously ran
        # credential-less; bind them to the first group.
        fallback = plans[0]
    for plan in plans:
        plan["env_base_url"] = env_base_url
    for index, plan in enumerate(plans):
        plan["key"] = _CONNECTION_KEY if index == 0 else f"{_CONNECTION_KEY}-{index + 1}"
    binding: dict[str, str] = {}
    for workspace_id, token, endpoints in entries:
        target = by_token.get(token) if token else fallback
        if target is None:
            continue
        target["members"].append((workspace_id, endpoints))
        binding[workspace_id] = target["key"]
    return plans, binding


def _merge_endpoints(plan: dict[str, Any]) -> dict[str, str]:
    """First non-empty value per field wins; divergences are logged."""
    merged = {field: "" for field in _ENDPOINT_KEYS}
    merged["base_url"] = str(plan.get("env_base_url") or "")
    for workspace_id, endpoints in plan["members"]:
        for field in _ENDPOINT_KEYS:
            value = endpoints.get(field) or ""
            if not value:
                continue
            if merged[field] and merged[field] != value:
                logger.warning(
                    "external connections migration: workspace %s %s=%s diverges from"
                    " connection %s (%s); keeping the first value",
                    workspace_id,
                    field,
                    value,
                    plan["key"],
                    merged[field],
                )
            else:
                merged[field] = value
    return merged
