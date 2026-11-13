"""COMMIT-final lock and identity projection for artifact recovery (#1019).

The two try-locks are the final acquisition, followed by the final read. After
this function returns, the rerun transaction performs no more DB writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.workflow_worker.input_hydration import live_probe_names
from server.app.workflows.revision_format import serialize_definition
from server.app.workflows.sharding_batch import running_shard_nodes, shard_effective_statuses

_EMPTY_JOB_DIR = Path(__file__).resolve().with_name("__artifact_recovery_empty_job_dir__")


def _expected_job_state(guard: Any, definition: Any) -> dict[str, Any]:
    expected = dict(guard.entries[0]["job_state"])
    raw = dict(expected["node_statuses"])
    for key in guard.affected_nodes:
        raw[key] = "pending" if key in guard.seed_nodes else "stale"
    pending = sorted(set(expected["pending_shard_nodes"]) - set(guard.affected_nodes))
    running = running_shard_nodes(definition, raw)
    effective = shard_effective_statuses(raw, set(pending) & set(running))
    expected.update(
        status="queued",
        execution_generation=int(expected["execution_generation"]) + 1,
        node_statuses=dict(sorted(raw.items())),
        effective_node_statuses=dict(sorted(effective.items())),
        pending_shard_nodes=pending,
    )
    return expected


def validate_final_recovery(guard: Any, conn: Any) -> None:
    """Reject active writers and every change from the expected post-rerun state."""
    if not guard.queries.try_artifact_recovery_final_locks_in_transaction(
        conn, guard.job_id, guard.entries[0]["workspace_id"]
    ):
        guard._stale("manifest or revision writer is active")
    from server.app.services.job_rerun.recovery_plan import (
        job_state_projection,
        resolve_definition,
    )

    current = guard.queries.artifact_dangling_job_in_transaction(conn, guard.job_id)
    if current is None:
        guard._stale("job disappeared before COMMIT")
    definition, _ = resolve_definition(current)
    if definition is None or serialize_definition(definition) != guard.definition_json:
        guard._stale("active definition changed before COMMIT")
    assert definition is not None
    expected_state = _expected_job_state(guard, definition)
    current_state = job_state_projection(current)
    if current_state != expected_state:
        guard._stale("final job state differs from the single rerun transaction")
    expected_live = live_probe_names(
        definition, expected_state["effective_node_statuses"], _EMPTY_JOB_DIR
    )
    current_live = live_probe_names(
        definition, current_state["effective_node_statuses"], _EMPTY_JOB_DIR
    )
    if current_live != expected_live:
        guard._stale("final live hydration surface changed")
    remaining = {
        str(row["name"]) for row in current["artifacts"] if str(row["name"]) in guard.staged_names
    }
    if remaining:
        guard._stale(f"new manifest rows appeared after retire: {sorted(remaining)}")
