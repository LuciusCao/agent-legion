"""Backfill workflow revisions to remove deprecated node 'resources' field.

The node-level ``resources`` field was retired in favor of workspace node
config + vault. Old published revisions still carry the field, causing
``workflow_definition_from_dict`` to raise on startup during agent route
reconciliation. This script strips ``resources`` from every node in every
workflow revision, recomputes ``definition_hash``, and preserves any
``node_code_pins`` snapshot.

Usage:
    uv run python -m scripts.backfill_workflow_revision_resources [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from typing import Any

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction


def _strip_node_resources(nodes: dict[str, Any]) -> bool:
    """Remove 'resources' from each node; return True if any node changed."""
    changed = False
    for node in nodes.values():
        if not isinstance(node, dict):
            continue
        if "resources" in node:
            del node["resources"]
            changed = True
    return changed


def _serialize_payload(payload: dict[str, Any]) -> str:
    """Match the canonical serialization used by the app."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def backfill_workflow_revision_resources(
    database_dsn: DatabaseDsn, *, dry_run: bool = False
) -> tuple[int, int]:
    """Return (matched, updated) revision counts."""
    with read_connection(database_dsn) as conn:
        rows = conn.execute(
            "select id, workspace_id, workflow_key, version, status, definition_json"
            " from workflow_revisions"
            " order by workspace_id, workflow_key, version"
        ).fetchall()

    planned: list[tuple[str, str, str]] = []  # (id, new_json, new_hash)
    for row in rows:
        payload = json.loads(str(row["definition_json"]))
        nodes = payload.get("nodes")
        if not isinstance(nodes, dict):
            continue
        if not _strip_node_resources(nodes):
            continue

        node_code_pins = payload.pop("node_code_pins", None)
        definition_json = _serialize_payload(payload)
        new_hash = hashlib.sha256(definition_json.encode("utf-8")).hexdigest()
        if node_code_pins is not None:
            payload["node_code_pins"] = node_code_pins
            definition_json = _serialize_payload(payload)

        planned.append((str(row["id"]), definition_json, new_hash))

    if dry_run:
        return len(planned), 0

    updated = 0
    with write_transaction(database_dsn) as conn:
        for revision_id, definition_json, new_hash in planned:
            cursor = conn.execute(
                "update workflow_revisions"
                " set definition_json=%s, definition_hash=%s"
                " where id=%s and definition_hash!=%s",
                (definition_json, new_hash, revision_id, new_hash),
            )
            updated += cursor.rowcount

    return len(planned), updated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Strip deprecated node 'resources' from workflow revisions."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show matched rows without updating."
    )
    args = parser.parse_args()

    import os
    from pathlib import Path

    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip('"').strip("'")

    database_url = os.environ.get(
        "AGENT_LEGION_DATABASE_URL", "postgresql://127.0.0.1:5432/agent_legion"
    )
    database_dsn = DatabaseDsn(database_url)
    matched, updated = backfill_workflow_revision_resources(database_dsn, dry_run=args.dry_run)

    action = "Would update" if args.dry_run else "Updated"
    print(f"{action} {matched} workflow revision(s) (changed: {updated}).")


if __name__ == "__main__":
    main()
