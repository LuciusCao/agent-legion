"""Per-job ready evaluation and the definition cache for the workflow worker.

Extracted from ``server.app.workflow_worker.ready`` to keep that module
within its size budget. Snapshot JSON is immutable per
``workflow_definition_hash``, so parsed definitions are cached across poll
passes; the per-job reuse decision itself lives in the caller (dirty
tracking on lightweight job marks).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.services.node_code_pins import node_code_pins_from_job_snapshot
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.definition import WorkflowDefinition, WorkflowNode
from server.app.workflows.execution_control import allowed_nodes
from server.app.workflows.scheduler import find_ready_nodes
from server.app.workflows.workflow_branching import RUNNABLE_STATUSES

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReadyCandidate:
    """One ready (job, node) pair plus everything ``try_claim_and_submit`` needs."""

    definition: WorkflowDefinition
    job: dict[str, Any]
    node: WorkflowNode
    job_dir: Path
    control_snapshot: dict[str, Any]
    allowed: frozenset[str]


def resolve_cached_definition(
    worker: WorkflowWorkerThread,
    fallback: WorkflowDefinition | None,
    job: dict[str, Any],
) -> WorkflowDefinition | None:
    """The job's snapshot definition, parsed once per definition hash.

    Snapshot JSON is immutable per hash, so the cache never needs
    invalidation; with thousands of queued jobs sharing a handful of
    revisions this avoids re-parsing the same multi-KB definition on every
    poll pass. Falls back to the registered definition when the job carries
    no snapshot; None when neither exists (registered workflow without a
    catalog definition).
    """
    key = str(job.get("workflow_definition_hash") or "")
    if not key:
        return fallback
    if key not in worker.state.definition_cache:
        snapshot = str(job.get("workflow_definition_snapshot_json") or "")
        if not snapshot:
            snapshot = worker.job_db.get_workflow_snapshot_for_hash(key)
        worker.state.definition_cache[key] = (
            definition_from_job_snapshot({"workflow_definition_snapshot_json": snapshot})
            if snapshot
            else None
        )
    return worker.state.definition_cache[key] or fallback


def evaluate_job_ready(
    worker: WorkflowWorkerThread,
    definition: WorkflowDefinition,
    job: dict[str, Any],
    statuses: dict[str, str],
    *,
    branch_not_applicable: set[str],
    pending_shard_nodes: set[str],
) -> list[ReadyCandidate]:
    """Evaluate one changed job and return its ready nodes.

    Branch and shard-pending inputs are precomputed by the caller so this
    function needs no database round trips.
    """
    job_dir = resolve_job_dir(job, worker.settings.jobs_dir)
    control_snapshot = {
        "execution_mode": job.get("execution_mode", "full"),
        "target_node_key": job.get("target_node_key"),
        "execution_paused": bool(job.get("execution_paused")),
        "pause_reason": job.get("pause_reason", ""),
    }
    for key in branch_not_applicable:
        if statuses.get(key) in RUNNABLE_STATUSES:
            statuses[key] = "not_applicable"
    try:
        allowed = allowed_nodes(definition, control_snapshot)
    except Exception:
        logger.exception("failed to compute allowed nodes for job %s", job["id"])
        return []
    for node in definition.nodes.values():
        if (
            node.shard is not None
            and statuses.get(node.key) == "running"
            and node.key in pending_shard_nodes
        ):
            statuses[node.key] = "pending"
    # The multi-KB snapshot text is dropped, but its small node_code_pins
    # stay on the lean job: quality-replay batches pin them (snapshot pins
    # win over the intake batch's node_code_versions); ordinary jobs ignore
    # them and dispatch the latest published code (#115).
    lean_job = {
        **job,
        "workflow_definition_snapshot_json": "",
        "node_code_pins": node_code_pins_from_job_snapshot(job),
    }
    candidates: list[ReadyCandidate] = []
    for node in find_ready_nodes(definition, statuses, job_dir):
        if node.key in allowed:
            candidates.append(
                ReadyCandidate(
                    definition=definition,
                    job=lean_job,
                    node=node,
                    job_dir=job_dir,
                    control_snapshot=control_snapshot,
                    allowed=allowed,
                )
            )
    return candidates
