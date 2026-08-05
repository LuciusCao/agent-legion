"""Agent catalog cutover (schema v27).

Drops the YAML-synced ``agent_definitions`` table (all readers moved to
``versioned_entities``) and flips every published Agent definition pinned to
``runtime='pi'`` to ``runtime='velites'``: the runtime now pins the command
builder directly and the 4 video agents that still declared ``pi`` ran
through velites via the retired ``workflows.pi.flavor`` switch anyway.

The flip publishes a new immutable version (old version archived) so in-flight
manifests pinned to an old definition hash fail loudly instead of silently
switching harness. Idempotent on replay: after one run no published row
carries ``runtime='pi'``, so the update loop matches nothing.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

_DROP_LEGACY_TABLE = "drop table if exists agent_definitions"

_PUBLISHED_PI_ROWS = """
select id, entity_key, definition_json
from versioned_entities
where entity_type='agent' and workspace_id is null and status='published'
  and definition_json::jsonb->>'runtime' = 'pi'
"""


def _canonical_hash(definition: dict[str, Any]) -> tuple[str, str]:
    canonical = json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), canonical


def migrate_agent_catalog_cutover(conn: Any) -> None:
    """Drop the legacy table and re-publish pi Agents as velites (v27)."""
    conn.execute(_DROP_LEGACY_TABLE)
    for row in conn.execute(_PUBLISHED_PI_ROWS).fetchall():
        definition = json.loads(str(row["definition_json"]))
        definition["runtime"] = "velites"
        definition_hash, canonical = _canonical_hash(definition)
        latest = conn.execute(
            "select max(version) as v from versioned_entities"
            " where entity_type='agent' and workspace_id is null and entity_key=%s",
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
            ) values (%s, 'agent', null, %s, %s, 'published', %s, %s, 'system',
                      current_timestamp, current_timestamp)
            on conflict do nothing
            """,
            (
                f"agent:{row['entity_key']}:v{new_version}",
                row["entity_key"],
                new_version,
                canonical,
                definition_hash,
            ),
        )
