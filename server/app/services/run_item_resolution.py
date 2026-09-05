"""Run item resolution helpers (#467 A1).

Split out of ``run_service.py`` for the file-size budget (same precedent as
``run_bundle_candidate``). The resolvers map items to job candidates using
chunked set-based existence probes behind the JobQueries facade
(BOUNDARY-DATA-001: ``run_item_probes`` + ``external_connections``); the old
shape ran one query per item.
"""

from __future__ import annotations

from typing import Any

from server.app.services.job_errors import InvalidOperationError, NotFoundError


def resolve_run_items(job_db: Any, workspace_id: str, items: list[dict[str, Any]]) -> list[dict]:
    """Resolve items into job candidates, preserving item order.

    Error mapping per item type is the pre-chunking contract verbatim
    (material not found → 404; not ready → 400; bundle not fully ready →
    400; unknown/disabled connection key → 400). Shape errors (non-object
    items, missing ids, unsupported types) raise before any probe runs.
    """
    material_specs: list[tuple[int, str]] = []
    bundle_specs: list[tuple[int, str]] = []
    ref_specs: list[tuple[int, dict[str, Any]]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise InvalidOperationError("Each item must be an object")
        item_type = item.get("type")
        if item_type == "material":
            material_id = str(item.get("material_id") or "").strip()
            if not material_id:
                raise InvalidOperationError("material item requires material_id")
            material_specs.append((index, material_id))
        elif item_type == "bundle":
            bundle_id = str(item.get("bundle_id") or "").strip()
            if not bundle_id:
                raise InvalidOperationError("bundle item requires bundle_id")
            bundle_specs.append((index, bundle_id))
        elif item_type == "ref":
            connection_key = str(item.get("connection_key") or "").strip()
            external_id = str(item.get("external_id") or "").strip()
            if not connection_key or not external_id:
                raise InvalidOperationError("ref item requires connection_key and external_id")
            ref_specs.append((index, item))
        else:
            raise InvalidOperationError(f"Unsupported item type: {item_type!r}")

    resolved: dict[int, dict[str, Any]] = {}
    _resolve_materials(job_db, workspace_id, material_specs, resolved)
    _resolve_bundles(job_db, workspace_id, bundle_specs, resolved)
    _resolve_refs(job_db, ref_specs, resolved)
    return [resolved[index] for index in sorted(resolved)]


def _resolve_materials(
    job_db: Any, workspace_id: str, specs: list[tuple[int, str]], resolved: dict[int, dict]
) -> None:
    by_id = job_db.fetch_materials_by_ids(workspace_id, [m_id for _, m_id in specs])
    for index, material_id in specs:
        row = by_id.get(material_id)
        if row is None:
            raise NotFoundError(f"Material not found: {material_id}")
        if str(row["status"]) != "ready":
            raise InvalidOperationError(
                f"Material is not ready: {material_id} (status: {row['status']})"
            )
        resolved[index] = {
            "entity_type": "material",
            "entity_id": material_id,
            "title": str(row["filename"]),
            "stem": "",
            "input": {"type": "material", "material_id": material_id},
        }


def _resolve_bundles(
    job_db: Any, workspace_id: str, specs: list[tuple[int, str]], resolved: dict[int, dict]
) -> None:
    by_id = job_db.fetch_bundles_by_ids(workspace_id, [b_id for _, b_id in specs])
    for index, bundle_id in specs:
        row = by_id.get(bundle_id)
        if row is None:
            raise NotFoundError(f"Material bundle not found: {bundle_id}")
        ready_count = int(row["ready_count"])
        if ready_count != int(row["file_count"]):
            raise InvalidOperationError(
                f"Material bundle is not fully ready: {bundle_id}"
                f" ({ready_count}/{row['file_count']} members ready)"
            )
        resolved[index] = {
            "entity_type": "bundle",
            "entity_id": bundle_id,
            "title": str(row["name"]),
            "stem": "",
            "input": {"type": "bundle", "bundle_id": bundle_id},
        }


def _resolve_refs(
    job_db: Any, specs: list[tuple[int, dict[str, Any]]], resolved: dict[int, dict]
) -> None:
    # Instance-level connections shared across workspaces
    # (SECURITY-EXTERNAL-CONNECTION-001). Existence AND enabled state are
    # checked here (#425 review) in one chunked probe over the distinct
    # keys (key/enabled only — no config material).
    enabled_by_key = job_db.external_connection_enabled_map(
        list(dict.fromkeys(str(item.get("connection_key")) for _, item in specs))
    )
    for index, item in specs:
        connection_key = str(item.get("connection_key") or "").strip()
        external_id = str(item.get("external_id") or "").strip()
        enabled = enabled_by_key.get(connection_key)
        if enabled is None:
            raise InvalidOperationError(f"Unknown connection key: {connection_key}")
        if not enabled:
            raise InvalidOperationError(f"Connection is disabled: {connection_key}")
        # Ref identity is connection-scoped: the same external_id reachable
        # through two connections denotes two distinct items, so the dedup
        # key, the job id and cross-request dedup all derive from
        # connection_key + external_id (a bare external_id would silently
        # drop the second connection's item as a duplicate).
        resolved[index] = {
            "entity_type": "ref",
            "entity_id": f"{connection_key}:{external_id}",
            "title": external_id,
            "stem": "",
            "input": dict(item),
        }
