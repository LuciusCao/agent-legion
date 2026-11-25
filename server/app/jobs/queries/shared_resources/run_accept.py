"""Atomic current-selection resolution and run acceptance (#1553 M43)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from server.app.jobs.queries.batch_queue_sql import RUN_UPSERT_CONFLICT
from server.app.jobs.queries.connection import ConnectionQueriesMixin
from server.app.jobs.queries.run_healing import deterministic_run_id
from server.app.workflows.shared_resources import (
    make_resource_freeze,
    resource_freeze_from_run,
)


def _source(row: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = json.loads(str(row["source_snapshot_json"]))
    return {
        "type": str(row["source_type"]),
        "id": str(row["source_id"]),
        "workspace_id": str(row["source_workspace_id"]),
        "materials": [
            {
                "material_id": str(file["source_material_id"]),
                "path": str(file["path"]),
                "upload_generation": int(file["source_upload_generation"]),
                "sha256": str(file["sha256"]),
                "size_bytes": int(file["size_bytes"]),
            }
            for file in snapshot["files"]
        ],
    }


def resolve_current_resource_pins_tx(
    conn: Any, workspace_id: str, declarations: Mapping[str, list[str]]
) -> dict[str, Any]:
    """Resolve every declaration against one transaction's locked selections.

    Grant key-share locks serialize with selection CAS and revoke; version
    key-share locks serialize with logical deletion. No latest query exists.
    """
    pins: list[dict[str, Any]] = []
    for resource_id in sorted({rid for ids in declarations.values() for rid in ids}):
        row = conn.execute(
            "select r.name, g.id as grant_id, s.revision as binding_revision,"
            " v.id as version_id, v.resource_id, v.version, v.manifest_hash,"
            " v.source_type, v.source_id, v.source_workspace_id, v.source_snapshot_json"
            " from shared_resource_grants g"
            " join shared_resource_selections s on s.grant_id=g.id"
            " join shared_resource_versions v on v.id=s.version_id"
            " join shared_resources r on r.id=g.resource_id"
            " where g.workspace_id=%s and g.resource_id=%s and g.status='active'"
            " and v.status='ready' and v.deleted_at is null"
            " for key share of g, v",
            (workspace_id, resource_id),
        ).fetchone()
        if row is None:
            raise ValueError("Shared resource is unavailable or revoked for new runs")
        files = conn.execute(
            "select path, sha256, size_bytes, content_type from shared_resource_files"
            " where version_id=%s order by ordinal",
            (row["version_id"],),
        ).fetchall()
        pins.append(
            {
                "resource_id": resource_id,
                "name": str(row["name"]),
                "version_id": str(row["version_id"]),
                "version": int(row["version"]),
                "manifest_hash": str(row["manifest_hash"]),
                "grant_id": str(row["grant_id"]),
                "binding_revision": int(row["binding_revision"]),
                "source": _source(row),
                "files": [
                    {
                        "path": str(file["path"]),
                        "sha256": str(file["sha256"]),
                        "size_bytes": int(file["size_bytes"]),
                        "content_type": str(file["content_type"]),
                    }
                    for file in files
                ],
            }
        )
    return make_resource_freeze(declarations, pins)


def run_pin_reference_count_tx(conn: Any, version_id: str) -> int:
    row = conn.execute(
        "select count(*) as count from runs"
        " where frozen_pins_json::jsonb #> '{shared_resources,pins}' @> %s::jsonb",
        (json.dumps([{"version_id": version_id}]),),
    ).fetchone()
    return int(row["count"])


def accepted_resource_pin(
    run: Mapping[str, Any],
    *,
    resource_id: str,
    version_id: str,
    manifest_hash: str,
    grant_id: str,
) -> bool:
    freeze = resource_freeze_from_run(run)
    return any(
        str(pin.get("resource_id")) == resource_id
        and str(pin.get("version_id")) == version_id
        and str(pin.get("manifest_hash")) == manifest_hash
        and str(pin.get("grant_id")) == grant_id
        for pin in freeze.get("pins", [])
        if isinstance(pin, Mapping)
    )


def create_resource_run_tx(
    conn: Any,
    *,
    workflow_key: str,
    source_kind: str,
    digest_payload: dict[str, Any],
    workspace_id: str,
    status: str,
    frozen_pins: dict[str, Any],
    declarations: Mapping[str, list[str]],
) -> tuple[dict[str, Any], bool]:
    freeze = resolve_current_resource_pins_tx(conn, workspace_id, declarations)
    digest = {**digest_payload, "shared_resource_pins": freeze["pins_hash"]}
    run_id = deterministic_run_id(workspace_id, workflow_key, source_kind, digest)
    pins_json = json.dumps(
        {**frozen_pins, "shared_resources": freeze}, ensure_ascii=False, sort_keys=True
    )
    row = conn.execute(
        f"""
        insert into runs(id, workspace_id, source_kind, status, frozen_pins_json, queue_payload_json)
        values (%s, %s, %s, %s, %s, '')
        {RUN_UPSERT_CONFLICT}
        returning *, (xmax = 0) as inserted
        """,
        (run_id, workspace_id, source_kind, status, pins_json),
    ).fetchone()
    if row is None:
        raise RuntimeError("resource run upsert did not return a row")
    created = bool(row["inserted"])
    result = dict(row)
    result.pop("inserted", None)
    return result, created


def inherit_replay_resource_freeze(
    conn: Any, workspace_id: str, frozen_pins: dict[str, Any]
) -> dict[str, Any]:
    replay = frozen_pins.get("quality_replay")
    source_job_id = str(replay.get("source_job_id") or "") if isinstance(replay, Mapping) else ""
    if not source_job_id or "shared_resources" in frozen_pins:
        return frozen_pins
    row = conn.execute(
        "select r.frozen_pins_json from jobs j join runs r on r.id=j.run_id"
        " where j.id=%s and j.workspace_id=%s and r.workspace_id=%s",
        (source_job_id, workspace_id, workspace_id),
    ).fetchone()
    if row is None:
        return frozen_pins
    freeze = resource_freeze_from_run(row)
    return {**frozen_pins, "shared_resources": freeze} if freeze else frozen_pins


class SharedResourceRunQueriesMixin(ConnectionQueriesMixin):
    def create_resource_run(
        self,
        workflow_key: str,
        source_kind: str,
        digest_payload: dict[str, Any],
        *,
        workspace_id: str,
        status: str,
        frozen_pins: dict[str, Any],
        declarations: Mapping[str, list[str]],
    ) -> tuple[dict[str, Any], bool]:
        with self.connect() as conn:
            return create_resource_run_tx(
                conn,
                workflow_key=workflow_key,
                source_kind=source_kind,
                digest_payload=digest_payload,
                workspace_id=workspace_id,
                status=status,
                frozen_pins=frozen_pins,
                declarations=declarations,
            )
