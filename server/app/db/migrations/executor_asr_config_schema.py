"""transcribe_video ASR config schema (schema v31).

The yaml ``asr:`` section (the last ``config/agent_legion.yaml`` key) is
retired: its business parameters (``provider`` / ``timeout_seconds``) move
into the ``transcribe_video`` capability ``config_schema`` of the built-in
``code-default`` executor definition, and the machine-local binary/model
paths become env-only (``AGENT_LEGION_ASR_*``). Deployments seeded at v30
carry a published ``code-default`` row without that schema, so re-publish it
with the schema properties merged in (existing keys win — admin edits are
never overwritten). The re-publish stores a new immutable version (old
version archived), matching the built-in definition upgrade pattern.
Idempotent on replay: after one run every published row already carries both
properties, so the loop matches nothing.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Frozen snapshot of the v31 factory schema properties (migrations stay free
# of service/executor imports so later catalog edits cannot rewrite history).
_ASR_SCHEMA_PROPERTIES: dict[str, Any] = {
    "provider": {
        "type": "string",
        "enum": ["auto", "whisper", "sensevoice"],
        "default": "auto",
        "description": "ASR 提供方选择（出厂默认值，可被节点/workspace 覆盖）",
    },
    "timeout_seconds": {
        "type": "integer",
        "minimum": 1,
        "default": 900,
        "description": "单次转写超时秒数（出厂默认值，可被节点/workspace 覆盖）",
    },
}

_PUBLISHED_CODE_DEFAULT_ROWS = """
select id, entity_key, definition_json
from versioned_entities
where entity_type='executor' and workspace_id is null and status='published'
  and entity_key='code-default'
"""


def _canonical_hash(definition: dict[str, Any]) -> tuple[str, str]:
    canonical = json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), canonical


def migrate_executor_asr_config_schema(conn: Any) -> None:
    """Merge the ASR config_schema into the published code-default executor (v31)."""
    for row in conn.execute(_PUBLISHED_CODE_DEFAULT_ROWS).fetchall():
        definition = json.loads(str(row["definition_json"]))
        capabilities = definition.get("capabilities")
        capability = (
            capabilities.get("transcribe_video") if isinstance(capabilities, dict) else None
        )
        if not isinstance(capability, dict):
            continue
        schema = capability.get("config_schema")
        properties = schema.get("properties") if isinstance(schema, dict) else None
        merged = {**_ASR_SCHEMA_PROPERTIES, **(properties or {})}
        if properties is not None and merged == properties:
            # Already carries at least the v31 properties (existing keys win).
            continue
        capability["config_schema"] = {"type": "object", "properties": merged}
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
