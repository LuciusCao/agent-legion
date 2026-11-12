"""Runs async mode service (#1117): accept + batch handle + control plane.

Split out of ``run_service.py`` for the file-size budget; the public entry
points stay on ``RunService`` (thin delegators) so routes keep one service.
Design: docs/architecture/runs-async-mode.md.

Accept does every CHEAP fail-fast check synchronously (workspace / active
revision / item shape + entry contract / node config resolution + code freeze
/ text-storage precondition), then persists the deterministic run row and the
``run_batches`` state row and returns the 202 handle. Per-item resolution and
dedup are deferred to the feeder — an all-duplicates async submission is a
202 whose batch completes with ``jobs_created=0`` (the sync path's "No tasks
were resolved" 400 does not cross modes; the #501 failed-run heal DOES, via
the shared digest).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from server.app.db.rowmap import iso_optional
from server.app.jobs import JobQueries
from server.app.services.job_errors import ConflictError, InvalidOperationError, NotFoundError
from server.app.services.job_intake_workspace import get_workspace
from server.app.services.run_item_client_token import drop_null_client_tokens
from server.app.services.run_item_types import validate_run_item_types
from server.app.services.run_text_items import is_text_item
from server.app.workflows.definition import workflow_definition_from_dict

if TYPE_CHECKING:
    from server.app.services.run_service import RunService


def prepare_submission(
    service: RunService, workspace_id: str, workflow_key: str, items: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any], Any, list[dict[str, Any]]]:
    """The submission preamble shared by the sync and async paths (#1117):
    workspace/revision/definition resolution and every cheap fail-fast item
    check, in the original order (behavior identical across modes). Lives
    here for run_service.py's file budget.
    """
    workspace = get_workspace(service.job_db, workspace_id)
    active_revision = service.job_db.get_active_workflow_revision(workspace_id, workflow_key)
    if active_revision is None:
        raise InvalidOperationError(
            "Workspace has no active workflow revision; publish a workflow revision first"
        )
    definition = workflow_definition_from_dict(json.loads(active_revision["definition_json"]))
    if not items:
        raise InvalidOperationError("At least one item is required")
    # #813: a null client_token means "no token" — normalize before the
    # digest so null and omitted resolve to the same run id.
    items = drop_null_client_tokens(items)
    max_items = service.settings.executor_runtime.workflows.max_items_per_run
    if max_items and len(items) > max_items:
        raise InvalidOperationError(
            f"Run items exceed the per-run limit: {len(items)} > {max_items}."
            " Split the submission into smaller runs (the limit is"
            " workflows.max_items_per_run in instance settings)."
        )
    validate_run_item_types(definition, items)
    # Runtime profile (#359): intake-stage rate gauges. Counted after the
    # cheap contract checks but before the heavy resolution/insert path,
    # so the gauge measures submitted load (what a campaign operator
    # pacing batches cares about), not just accepted load.
    from server.app.services.runtime_profile import profile

    profile.note_run_intake(len(items))
    return workspace, active_revision, definition, items


def batch_record(row: dict[str, Any]) -> dict[str, Any]:
    """Public batch handle; target_spec (frozen items + config) stays internal."""
    return {
        "run_id": str(row["run_id"]),
        "status": str(row["status"]),
        "items_total": int(row["items_total"]),
        "processed_count": int(row["next_index"]),
        "jobs_created": int(row["jobs_created"]),
        "chunk_errors": row["chunk_errors_json"]
        if isinstance(row["chunk_errors_json"], list)
        else json.loads(str(row["chunk_errors_json"] or "[]")),
        "error_message": str(row["error_message"] or ""),
        "created_at": iso_optional(row["created_at"]),
        "updated_at": iso_optional(row["updated_at"]),
        "finished_at": iso_optional(row["finished_at"]),
    }


def batch_response(run_row: dict[str, Any], batch_row: dict[str, Any]) -> dict[str, Any]:
    """The shared {run, batch} payload for accept and control responses."""
    from server.app.services.run_service import _run_record

    return {"run": _run_record(run_row), "batch": batch_record(batch_row)}


def accept_async_response(
    service: RunService,
    workspace_id: str,
    *,
    workflow_key: str,
    items: list[dict[str, Any]],
    created_by: str,
) -> dict[str, Any]:
    """accept_async_run + response assembly (RunService.create_run_async's body)."""
    return batch_response(
        *accept_async_run(
            service, workspace_id, workflow_key=workflow_key, items=items, created_by=created_by
        )
    )


def accept_async_run(
    service: RunService,
    workspace_id: str,
    *,
    workflow_key: str,
    items: list[dict[str, Any]],
    created_by: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Persist the async submission; returns (run row, batch row).

    Runs the shared submission preamble (``prepare_submission``: revision/
    definition + cheap item checks), then derives the SAME digest payload as
    the sync path (identical items map to the same deterministic run id in
    both modes), upserts the run row, then upserts + conditionally resets
    the batch row (failed/cancelled heal, completed-but-depleted requeue —
    both guarded single UPDATEs, so a blind retry racing an in-flight feed
    is a no-op, never a double-feed).
    """
    from server.app.services.run_service import ITEMS_SOURCE_KIND

    prepared = prepare_submission(service, workspace_id, workflow_key, items)
    workspace, active_revision, definition, items = prepared
    if any(is_text_item(item) for item in items) and (
        service.materials_service is None or service.materials_service.storage is None
    ):
        # Same fail-closed precondition as the sync path's materialize step
        # (materials API 503 contract); checked before any write.
        from server.app.services.materials import MaterialStorageUnavailableError

        raise MaterialStorageUnavailableError(
            "Material storage is not configured on this instance "
            "(AGENT_LEGION_S3_BUCKET is unset); text items cannot be stored"
        )
    node_config, node_code_versions = service.pipeline.resolve_node_setup(
        definition, workspace, workspace_id, workflow_key
    )
    digest_payload = {
        "workflow_key": workflow_key,
        "source_kind": ITEMS_SOURCE_KIND,
        "items": items,
        "node_config": node_config,
    }
    run = service.job_db.create_run(
        workflow_key,
        ITEMS_SOURCE_KIND,
        digest_payload,
        workspace_id=workspace_id,
        status="created",
        frozen_pins={"node_code_versions": node_code_versions},
    )
    start_node = definition.start_node
    text_input = start_node.text_input if start_node is not None else None
    target_spec = {
        "items": items,
        "workflow_revision_id": str(active_revision["id"]),
        "node_config": node_config,
        "node_code_versions": node_code_versions,
        "text_default_filename": text_input.filename if text_input is not None else "",
    }
    service.job_db.create_run_batch(
        str(run["id"]), workspace_id, target_spec, len(items), created_by
    )
    reset = service.job_db.reset_run_batch_for_resubmit(str(run["id"]), target_spec, len(items))
    if reset is not None:
        # The terminal-state heal reopens the batch; re-align the run row
        # immediately (feeder advances recompute it anyway, but the handle
        # must not read completed/failed while a fresh feed is queued).
        run = service.job_db.update_intake_run(
            str(run["id"]),
            created_count=service.job_db.count_jobs_in_run(str(run["id"])),
            status="created",
            error_message="",
        )
    batch = service.job_db.get_run_batch(str(run["id"]))
    if batch is None:
        raise RuntimeError("run batch upsert did not return a row")
    return run, batch


def get_run_batch_for_run(
    job_db: JobQueries, workspace_id: str, run_id: str
) -> dict[str, Any] | None:
    """The detail endpoint's optional batch handle (None for sync runs).

    Workspace binding is re-validated against the run row, not trusted from
    the batch row (the run lookup upstream already 404s cross-workspace ids).
    """
    row = job_db.get_run_batch(run_id)
    if row is None or str(row["workspace_id"]) != workspace_id:
        return None
    return row


def _transition(
    job_db: JobQueries,
    workspace_id: str,
    run_id: str,
    *,
    from_statuses: tuple[str, ...],
    to_status: str,
    mismatch: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """pause/resume/cancel shared body: guarded 404s, then the CAS.

    Returns (run row, batch row) on success. The CAS miss means the batch is
    in another state than the verb requires — a 409, not a silent no-op, so
    a paused-then-cancelled client learns the truth about its pause call.
    """
    run = job_db.get_run(run_id)
    if run is None or str(run["workspace_id"]) != workspace_id:
        raise NotFoundError("Run not found")
    batch = get_run_batch_for_run(job_db, workspace_id, run_id)
    if batch is None:
        raise NotFoundError("Run batch not found (run was created synchronously)")
    updated = job_db.transition_run_batch(run_id, from_statuses=from_statuses, to_status=to_status)
    if updated is None:
        raise ConflictError(f"{mismatch} (status: {batch['status']})")
    return run, updated


def pause_run_batch(job_db: JobQueries, workspace_id: str, run_id: str) -> tuple[dict, dict]:
    return _transition(
        job_db,
        workspace_id,
        run_id,
        from_statuses=("pending", "running"),
        to_status="paused",
        mismatch="Run batch is not feeding",
    )


def resume_run_batch(job_db: JobQueries, workspace_id: str, run_id: str) -> tuple[dict, dict]:
    return _transition(
        job_db,
        workspace_id,
        run_id,
        from_statuses=("paused",),
        to_status="pending",
        mismatch="Run batch is not paused",
    )


def cancel_run_batch(job_db: JobQueries, workspace_id: str, run_id: str) -> tuple[dict, dict]:
    """Stop feeding; already-created jobs stay (managed via the job batch ops)."""
    return _transition(
        job_db,
        workspace_id,
        run_id,
        from_statuses=("pending", "running", "paused"),
        to_status="cancelled",
        mismatch="Run batch is already terminal",
    )
