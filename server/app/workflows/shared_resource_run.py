"""RunService-facing shared-resource accept helper."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.services.job_errors import InvalidOperationError
from server.app.workflows.shared_resources import (
    declarations_for,
    resource_freeze_from_run,
    resource_pins_hash,
)


def require_run_api_for_shared_resources(definition: Any, allowed: bool) -> None:
    if declarations_for(definition) and not allowed:
        raise InvalidOperationError(
            "Shared resources require the /runs API; legacy /job-batches is not supported"
        )


def accept_resource_run(
    job_db: Any,
    *,
    workspace_id: str,
    workflow_key: str,
    source_kind: str,
    digest_payload: dict[str, Any],
    status: str,
    node_code_versions: dict[str, Any],
    declarations: Mapping[str, list[str]],
) -> tuple[dict[str, Any], bool, dict[str, Any]] | None:
    if not declarations:
        return None
    try:
        run, created = job_db.create_resource_run(
            workflow_key,
            source_kind,
            digest_payload,
            workspace_id=workspace_id,
            status=status,
            frozen_pins={"node_code_versions": node_code_versions},
            declarations=declarations,
        )
    except ValueError as exc:
        raise InvalidOperationError(str(exc)) from exc
    return run, created, resource_freeze_from_run(run)


def accept_items_run(
    job_db: Any,
    *,
    definition: Any,
    workspace_id: str,
    workflow_key: str,
    source_kind: str,
    digest_payload: dict[str, Any],
    status: str,
    node_code_versions: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    accepted = accept_resource_run(
        job_db,
        workspace_id=workspace_id,
        workflow_key=workflow_key,
        source_kind=source_kind,
        digest_payload=digest_payload,
        status=status,
        node_code_versions=node_code_versions,
        declarations=declarations_for(definition),
    )
    if accepted is not None:
        return accepted[0], resource_pins_hash(accepted[2])
    run = job_db.create_run(
        workflow_key,
        source_kind,
        digest_payload,
        workspace_id=workspace_id,
        status=status,
        frozen_pins={"node_code_versions": node_code_versions},
    )
    return run, ""
