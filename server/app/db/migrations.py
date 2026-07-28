"""Data migrations applied alongside the idempotent DDL replay."""

from __future__ import annotations

import contextlib
import json
from typing import Any

_DETAIL_KEY = "question_detail"
_LIST_KEY = "by_knowledge"


def _decode_json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _map_legacy_cms_config(cms: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map a legacy workspace cms_config onto resource binding configs."""
    mapped: dict[str, dict[str, Any]] = {_DETAIL_KEY: {}, _LIST_KEY: {}}
    if cms.get("question_detail_url"):
        mapped[_DETAIL_KEY]["api_url"] = cms["question_detail_url"]
    if cms.get("question_list_url"):
        mapped[_LIST_KEY]["api_url"] = cms["question_list_url"]
    if cms.get("api_url"):
        mapped[_DETAIL_KEY].setdefault("api_url", cms["api_url"])
        mapped[_LIST_KEY].setdefault("api_url", cms["api_url"])
    for key in ("token", "env", "bank_version", "country_id", "subject_id"):
        if cms.get(key) not in (None, ""):
            mapped[_DETAIL_KEY][key] = cms[key]
            mapped[_LIST_KEY][key] = cms[key]
    page_size = cms.get("page_size")
    if page_size not in (None, ""):
        with contextlib.suppress(TypeError, ValueError):
            mapped[_LIST_KEY]["page_size"] = int(str(page_size))
    return mapped


def migrate_workspace_cms_config(conn: Any) -> None:
    """Fold legacy workspace cms_config into resource_config bindings (v15).

    Existing binding config keys win; legacy values only fill gaps. Bindings
    created by the migration are enabled so resolution keeps behaving like the
    legacy workspace-level config did. Idempotent: a second run finds every
    key already present and writes nothing. Databases already upgraded past
    v15 no longer have the legacy column and are skipped.
    """
    columns = {
        row["column_name"]
        for row in conn.execute(
            "select column_name from information_schema.columns"
            " where table_schema=current_schema() and table_name='workspaces'"
        ).fetchall()
    }
    if "cms_config_json" not in columns:
        return
    rows = conn.execute(
        "select id, cms_config_json, resource_config_json from workspaces"
    ).fetchall()
    for row in rows:
        cms = _decode_json_object(row["cms_config_json"])
        if not cms:
            continue
        resource_config = _decode_json_object(row["resource_config_json"])
        existing_resources = resource_config.get("resources")
        resources = dict(existing_resources) if isinstance(existing_resources, dict) else {}

        changed = False
        for resource_key, config in _map_legacy_cms_config(cms).items():
            if not config:
                continue
            existing = resources.get(resource_key)
            binding = dict(existing) if isinstance(existing, dict) else {}
            raw_config = binding.get("config")
            binding_config = dict(raw_config) if isinstance(raw_config, dict) else {}
            added = False
            for key, value in config.items():
                if key not in binding_config:
                    binding_config[key] = value
                    added = True
            if not added:
                continue
            if "enabled" not in binding:
                binding["enabled"] = True
            binding["config"] = binding_config
            resources[resource_key] = binding
            changed = True

        if not changed:
            continue
        resource_config["resources"] = resources
        conn.execute(
            "update workspaces set resource_config_json=? where id=?",
            (json.dumps(resource_config, ensure_ascii=False, sort_keys=True), row["id"]),
        )
