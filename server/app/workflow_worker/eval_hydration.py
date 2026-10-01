"""Per-job hydration + branch evaluation pass for changed jobs (#759).

Split out of ``eval_batch`` for the file-size budget: that module owns the
batch skeleton (context collection, the batched shard-pending read, the
batched ``not_applicable`` write, ready evaluation); this module owns the
per-job middle pass — ready-gate hydration followed by branch evaluation —
including the shard-effective statuses hydration must probe with (#759
review P2).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from server.app.workflow_worker.input_hydration import hydrate_job_artifacts
from server.app.workflows.workflow_branching import RUNNABLE_STATUSES, evaluate_branches

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread

logger = logging.getLogger(__name__)


def hydrate_eval_contexts(
    worker: WorkflowWorkerThread,
    eval_contexts: list[dict[str, Any]],
    pending_shards_by_job: dict[str, set[str]],
) -> tuple[list[dict[str, Any]], list[tuple[str, list[str], str, int]]]:
    """Hydrate and branch-evaluate each collected context.

    Returns the surviving contexts (with ``branch_not_applicable`` filled in)
    and the batched not_applicable entries: (job_id, node_keys, reason,
    expected execution_generation) — the epoch the evaluation read (scan's
    fat job row); the batch write re-checks it under the job-mutation lock
    and skips stale entries (EXEC-GENERATION-001).
    """
    not_applicable_entries: list[tuple[str, list[str], str, int]] = []
    hydrated_contexts: list[dict[str, Any]] = []

    for ctx in eval_contexts:
        job = ctx["job"]
        statuses = ctx["statuses"]
        # Ready-gate hydration (#759 P1): manifest-backed inputs whose local
        # copy was evicted (EXEC-ARTIFACT-STORE-001) are re-materialized
        # BEFORE branch/ready evaluation — both probe the local filesystem
        # only, so a manifest-only input would otherwise park the job at
        # queued forever. A job whose manifest-backed inputs are still
        # missing after the attempt is NOT cached in job_evals (the next
        # poll pass re-evaluates and retries; a missing object may be
        # transient) and any stale cache entry is dropped so no candidate
        # built on the pre-eviction state can be claimed. #702 P1: hydration
        # brackets the restores with two jobs.execution_generation reads — a
        # reset mutation committing mid-flight invalidates the manifest rows
        # the restores came from, so a changed epoch discards exactly this
        # round's restored files and defers the job the same way (rounds
        # with an empty restore set skip the recheck read, #759 review P1).
        # The two READ failures (manifest query / generation pre-read) return
        # None and defer identically: a local miss must never be cached as a
        # true miss while the authoritative manifest is unreadable.
        #
        # #759 review P2: hydration probes with SHARD-EFFECTIVE statuses —
        # evaluate_job_ready flips a running shard node with pending shards
        # back to pending so the ready gate re-probes its inputs; hydration
        # must see the same flip, otherwise an evicted input of a mid-fanout
        # shard node is never restored and the remaining shards can never be
        # claimed. The flip rides a copy: branch evaluation and the ready
        # gate keep their own established status handling.
        flip = pending_shards_by_job.get(str(job["id"]))
        hydration_statuses = (
            {**statuses, **{node_key: "pending" for node_key in flip}} if flip else statuses
        )
        unrestored = hydrate_job_artifacts(
            worker.artifact_object_store,
            worker.job_db,
            job_id=str(job["id"]),
            job_dir=ctx["job_dir"],
            definition=ctx["definition"],
            node_statuses=hydration_statuses,
        )
        if unrestored is None or unrestored:
            worker.state.job_evals.pop(str(job["id"]), None)
            logger.warning(
                "job %s hydration incomplete (read failure, or unrestored inputs %s); "
                "evaluation deferred to the next poll pass",
                job["id"],
                sorted(unrestored) if unrestored else "-",
            )
            continue
        branch_evaluation = evaluate_branches(ctx["definition"], statuses, ctx["job_dir"])
        for key in branch_evaluation.not_applicable:
            if statuses.get(key) in RUNNABLE_STATUSES:
                statuses[key] = "not_applicable"
        ctx["branch_not_applicable"] = branch_evaluation.not_applicable
        hydrated_contexts.append(ctx)
        if branch_evaluation.not_applicable:
            not_applicable_entries.append(
                (
                    job["id"],
                    sorted(branch_evaluation.not_applicable),
                    "unselected workflow branch",
                    int(job.get("execution_generation") or 0),
                )
            )
    return hydrated_contexts, not_applicable_entries
