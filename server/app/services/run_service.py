"""RunService: create runs from items (materials-and-runs design §4, slice 3).

An item is the terminal intake state — a ready material upload or an external
reference — so creation skips the legacy RESOLVERS/source_kind validation
entirely while reusing the intake skeleton: node config freeze, code version
pins, dedup against existing jobs, deterministic run id digest, one job per
item. The legacy ``/job-batches`` path keeps flowing through
``JobIntakeService``; both creation paths stay fully usable until the intake
retirement slice.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from server.app.events import JobEventManager
from server.app.jobs import JobQueries
from server.app.scheduler_wakeup import notify_schedulable_work
from server.app.services.agent_service import published_agent_definitions
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.job_intake_workspace import get_workspace
from server.app.services.node_code_resolution import freeze_node_code_versions
from server.app.services.node_config import resolve_workflow_node_configs
from server.app.services.run_bundle_candidate import bundle_candidate
from server.app.services.run_item_types import validate_run_item_types
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.definition import workflow_definition_from_dict

logger = logging.getLogger(__name__)

# Runs created from items carry this marker in source_kind; legacy rows keep
# their intake source_kind for display (design §5.2).
ITEMS_SOURCE_KIND = "items"


def _parse_object(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or ""))
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _run_record(row: dict[str, Any]) -> dict[str, Any]:
    """Public run record; queue_payload (async intake working state) stays internal."""
    return {
        "id": str(row["id"]),
        "workspace_id": str(row["workspace_id"]),
        "workflow_key": str(row["workflow_key"]),
        "source_kind": str(row["source_kind"]),
        "status": str(row["status"]),
        "created_count": int(row["created_count"]),
        "error_message": str(row["error_message"] or ""),
        "frozen_pins": _parse_object(row.get("frozen_pins_json")),
        "stats": _parse_object(row.get("stats_json")),
        "created_by": str(row["created_by"] or ""),
        "created_at": _timestamp(row["created_at"]),
        "updated_at": _timestamp(row["updated_at"]),
    }


class RunService:
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        job_event_manager: JobEventManager | None = None,
        job_event_buffer: Any | None = None,
    ):
        self.job_db = job_db
        self.settings = settings
        self.job_event_manager = job_event_manager
        self.job_event_buffer = job_event_buffer

    def create_run(
        self,
        workspace_id: str,
        *,
        workflow_key: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        workspace = get_workspace(self.job_db, workspace_id)
        active_revision = self.job_db.get_active_workflow_revision(workspace_id, workflow_key)
        if active_revision is None:
            raise InvalidOperationError(
                "Workspace has no active workflow revision; publish a workflow revision first"
            )
        definition = workflow_definition_from_dict(json.loads(active_revision["definition_json"]))
        if not items:
            raise InvalidOperationError("At least one item is required")
        validate_run_item_types(definition, items)

        # Validate everything (items, node config, pins) before the first
        # write so a rejected request leaves no half-created run behind.
        candidates = self._resolve_items(workspace_id, items)
        try:
            node_config = resolve_workflow_node_configs(
                definition,
                published_agent_definitions(self.job_db, workspace_id),
                workspace,
            )
        except ValueError as exc:
            raise InvalidOperationError(f"Invalid node configuration: {exc}") from exc
        node_code_versions = freeze_node_code_versions(
            self.job_db,
            self.settings.executor_runtime.workflows.custom_nodes_enabled,
            workspace_id,
            workflow_key,
            list(definition.executable_nodes),
        )

        # Same dedup contract as intake: items whose (source_type, source_id)
        # already has a job in this workflow drop out; accepted keys grow the
        # set so intra-request duplicates filter exactly like pre-existing jobs.
        existing_keys = self.job_db.list_job_dedup_keys(workspace_id, workflow_key)
        fresh: list[dict[str, Any]] = []
        for candidate in candidates:
            key = (str(candidate["entity_type"]), str(candidate["entity_id"]))
            if key in existing_keys:
                continue
            existing_keys.add(key)
            fresh.append(candidate)
        if not fresh:
            raise InvalidOperationError("No tasks were resolved from input")

        digest_payload = {
            "workflow_key": workflow_key,
            "source_kind": ITEMS_SOURCE_KIND,
            "items": items,
            "node_config": node_config,
        }
        run = self.job_db.create_run(
            workflow_key,
            ITEMS_SOURCE_KIND,
            digest_payload,
            workspace_id=workspace_id,
            frozen_pins={"node_code_versions": node_code_versions},
        )
        try:
            jobs = self.job_db.create_jobs_bulk(
                candidates=fresh,
                workflow_key=workflow_key,
                run_id=run["id"],
                node_keys=list(definition.executable_nodes),
                workspace_id=workspace_id,
                revision=active_revision,
                frozen_config=node_config,
            )
        except Exception as exc:
            # A fresh item can still collide at insert time: two items can
            # normalize to the same job id (``a/b`` vs ``a_b``), or an item
            # can hit a legacy-path job with a different source_type but the
            # same source_id. The run row already committed (create_jobs_bulk
            # runs in its own transaction), so compensate instead of leaving
            # a half-created run behind.
            self._discard_empty_run(str(run["id"]))
            if isinstance(exc, ValueError):
                raise InvalidOperationError(str(exc)) from exc
            raise
        if jobs:
            notify_schedulable_work()
        # Persist the final creation progress so the run row alone answers
        # the detail endpoint (legacy sync intake kept this only in memory).
        run = self.job_db.update_intake_run(
            str(run["id"]), created_count=len(jobs), status=str(run["status"])
        )

        for job in jobs:
            job["storage_dir"] = str(resolve_job_dir(job, self.settings.jobs_dir))
            # Wire compatibility: API/SSE consumers still read ``batch_id``
            # (route renames are a later slice); the value is the run id.
            job["batch_id"] = str(job.get("run_id") or "")

        if self.job_event_buffer is not None:
            self.job_event_buffer.record_jobs_created(
                workspace_id, [str(job["id"]) for job in jobs]
            )
        elif self.job_event_manager is not None:
            stats = self.job_db.count_jobs_by_status(workspace_id)
            self.job_event_manager.broadcast_jobs_created(workspace_id, jobs, stats)
        return {"run": _run_record(run), "created_count": len(jobs), "jobs": jobs}

    def _discard_empty_run(self, run_id: str) -> None:
        # Best-effort cleanup of the run row after job creation failed;
        # never mask the original failure.
        try:
            self.job_db.delete_run_without_jobs(run_id)
        except OSError as exc:
            # #204: the compensation is one guarded DELETE via the JobQueries
            # facade — a DB connectivity failure here must not mask the
            # original creation error. Programming errors propagate (the
            # facade is exercised by every create_run test).
            logger.warning("run %s left orphaned after job creation failed: %s", run_id, exc)

    def list_runs(self, workspace_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.job_db.list_runs(workspace_id, limit)
        return [_run_record(row) for row in rows]

    def get_run(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        row = self.job_db.get_run(run_id)
        if row is None or str(row["workspace_id"]) != workspace_id:
            raise NotFoundError("Run not found")
        by_status = self.job_db.count_jobs_by_status_in_run(run_id)
        return {
            "run": _run_record(row),
            "job_stats": {"total": sum(by_status.values()), "by_status": by_status},
        }

    def _resolve_items(
        self, workspace_id: str, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                raise InvalidOperationError("Each item must be an object")
            item_type = item.get("type")
            if item_type == "material":
                candidates.append(self._material_candidate(workspace_id, item))
            elif item_type == "bundle":
                candidates.append(bundle_candidate(self.job_db, workspace_id, item))
            elif item_type == "ref":
                candidates.append(self._ref_candidate(item))
            else:
                raise InvalidOperationError(f"Unsupported item type: {item_type!r}")
        return candidates

    def _material_candidate(self, workspace_id: str, item: dict[str, Any]) -> dict[str, Any]:
        material_id = str(item.get("material_id") or "").strip()
        if not material_id:
            raise InvalidOperationError("material item requires material_id")
        with self.job_db.read() as conn:
            row = conn.execute(
                "select id, status, filename from materials where id=%s and workspace_id=%s",
                (material_id, workspace_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Material not found: {material_id}")
        if str(row["status"]) != "ready":
            raise InvalidOperationError(
                f"Material is not ready: {material_id} (status: {row['status']})"
            )
        return {
            "entity_type": "material",
            "entity_id": material_id,
            "title": str(row["filename"]),
            "stem": "",
            "input": dict(item),
        }

    def _ref_candidate(self, item: dict[str, Any]) -> dict[str, Any]:
        connection_key = str(item.get("connection_key") or "").strip()
        external_id = str(item.get("external_id") or "").strip()
        if not connection_key or not external_id:
            raise InvalidOperationError("ref item requires connection_key and external_id")
        # Connections are instance-level and shared across workspaces
        # (SECURITY-EXTERNAL-CONNECTION-001); direction validation
        # (CONNECT-DIRECTION-001) is a later slice, so existence is the whole
        # check here.
        with self.job_db.read() as conn:
            row = conn.execute(
                "select key from external_connections where key=%s",
                (connection_key,),
            ).fetchone()
        if row is None:
            raise InvalidOperationError(f"Unknown connection key: {connection_key}")
        # Ref identity is connection-scoped: the same external_id reachable
        # through two connections denotes two distinct items, so the dedup
        # key, the job id and cross-request dedup all derive from
        # connection_key + external_id (a bare external_id would silently
        # drop the second connection's item as a duplicate).
        return {
            "entity_type": "ref",
            "entity_id": f"{connection_key}:{external_id}",
            "title": external_id,
            "stem": "",
            "input": dict(item),
        }
