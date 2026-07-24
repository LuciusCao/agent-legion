"""Per-job ready evaluation and its caches for the workflow worker.

Extracted from ``server.app.workflow_worker_ready`` to keep that module
within its size budget. Snapshot JSON is immutable per
``workflow_definition_hash``, so parsed definitions are cached per poll pass;
jobs whose evaluation inputs (statuses, execution control, workflow hash) are
unchanged since the previous pass reuse their cached ready node keys instead
of re-running branch evaluation and per-input file stats. Jobs with a running
node are never cached: shard nodes can gain pending shards without any status
change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.definition import WorkflowDefinition, WorkflowNode
from server.app.workflows.execution_control import allowed_nodes
from server.app.workflows.scheduler import find_ready_nodes
from server.app.workflows.sharding import has_pending_shards
from server.app.workflows.workflow_branching import RUNNABLE_STATUSES, evaluate_branches

if TYPE_CHECKING:
    from server.app.workflow_worker_thread import WorkflowWorkerThread

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


def cached_snapshot_definition(
    worker: WorkflowWorkerThread, job: dict[str, Any]
) -> WorkflowDefinition | None:
    """Parse the job's workflow snapshot, cached by workflow_definition_hash.

    Snapshot JSON is immutable per hash, so the cache never needs
    invalidation; with thousands of queued jobs this avoids re-parsing the
    same multi-KB definition on every poll pass.
    """
    if not (job.get("workflow_definition_snapshot_json") or ""):
        return None
    key = str(job.get("workflow_definition_hash") or "")
    if not key:
        return definition_from_job_snapshot(job)
    if key not in worker._definition_cache:
        worker._definition_cache[key] = definition_from_job_snapshot(job)
    return worker._definition_cache[key]


def evaluation_fingerprint(job: dict[str, Any], statuses: dict[str, str]) -> tuple[Any, ...]:
    """All inputs that can change a job's ready-node evaluation.

    Node artifacts are written before the owning node's status flips, so a
    fingerprint hit means branch evaluation and input checks would reach the
    same conclusion without touching disk.
    """
    return (
        job.get("status"),
        bool(job.get("execution_paused")),
        job.get("execution_mode"),
        job.get("target_node_key"),
        job.get("workflow_definition_hash"),
        tuple(sorted(statuses.items())),
    )


def evaluate_job_ready(
    worker: WorkflowWorkerThread,
    definition: WorkflowDefinition,
    job: dict[str, Any],
    statuses: dict[str, str],
) -> list[ReadyCandidate]:
    """Evaluate one runnable job, reusing the cached result when unchanged.

    Semantics match the previous per-round evaluation: same branch
    evaluation, same ``allowed_nodes`` filtering and ``find_ready_nodes``
    ordering. ``mark_nodes_not_applicable`` still runs per job; its effect is
    mirrored into the in-memory statuses instead of re-querying them.
    """
    eval_stats = worker._last_ready_stats
    definition_to_run = cached_snapshot_definition(worker, job) or definition
    job_dir = resolve_job_dir(job, worker.settings.jobs_dir)
    control_snapshot = {
        "execution_mode": job.get("execution_mode", "full"),
        "target_node_key": job.get("target_node_key"),
        "execution_paused": bool(job.get("execution_paused")),
        "pause_reason": job.get("pause_reason", ""),
    }
    cacheable = "running" not in statuses.values()
    if not cacheable:
        eval_stats["running"] = eval_stats.get("running", 0) + 1
    fingerprint = evaluation_fingerprint(job, statuses)
    if cacheable:
        cached = worker._ready_cache.get(job["id"])
        if cached is not None and cached[0] == fingerprint:
            eval_stats["hit"] += 1
            _, cached_node_keys, allowed = cached
            candidates: list[ReadyCandidate] = []
            for key in cached_node_keys:
                node = definition_to_run.nodes.get(key)
                if node is not None:
                    candidates.append(
                        ReadyCandidate(
                            definition=definition_to_run,
                            job=job,
                            node=node,
                            job_dir=job_dir,
                            control_snapshot=control_snapshot,
                            allowed=allowed,
                        )
                    )
            return candidates
    eval_stats["miss"] += 1
    branch_evaluation = evaluate_branches(definition_to_run, statuses, job_dir)
    worker.job_db.mark_nodes_not_applicable(
        job["id"],
        sorted(branch_evaluation.not_applicable),
        "unselected workflow branch",
    )
    # Mirror the update in memory instead of re-querying statuses:
    # mark_nodes_not_applicable only rewrites rows whose status is still
    # runnable, so only those keys flip to not_applicable.
    for key in branch_evaluation.not_applicable:
        if statuses.get(key) in RUNNABLE_STATUSES:
            statuses[key] = "not_applicable"
    try:
        allowed = allowed_nodes(definition_to_run, control_snapshot)
    except Exception:
        logger.exception("failed to compute allowed nodes for job %s", job["id"])
        return []
    # A shard node whose aggregate row sits in 'running' may still hold
    # pending shards; mirror it as runnable so the rest of them dispatch.
    for node in definition_to_run.nodes.values():
        if node.shard is not None and statuses.get(node.key) == "running":
            with worker.job_db._connect_read() as conn:
                if has_pending_shards(conn, job["id"], node.key):
                    statuses[node.key] = "pending"
    candidates = []
    ready_node_keys: list[str] = []
    for node in find_ready_nodes(definition_to_run, statuses, job_dir):
        if node.key in allowed:
            candidates.append(
                ReadyCandidate(
                    definition=definition_to_run,
                    job=job,
                    node=node,
                    job_dir=job_dir,
                    control_snapshot=control_snapshot,
                    allowed=allowed,
                )
            )
            ready_node_keys.append(node.key)
    if cacheable:
        # Recompute the fingerprint: mark_nodes_not_applicable may have
        # flipped statuses after the pre-evaluation fingerprint was taken.
        worker._ready_cache[job["id"]] = (
            evaluation_fingerprint(job, statuses),
            ready_node_keys,
            allowed,
        )
    return candidates
