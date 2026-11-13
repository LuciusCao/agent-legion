"""Shared dangling-artifact plan pins and their rerun-transaction guard (#1019).

The dry-run builder and apply use this same state projection. The guard is
optional: ordinary reruns do not carry a reviewed plan and keep their existing
behavior. All database access goes through the JobQueries facade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn

from server.app.jobs.atomic_mutations import JobMutationConflict
from server.app.services.hydration_defer_board import defer_scope
from server.app.services.job_artifact_staging_scope import staging_output_names
from server.app.services.job_reset_closure import rerun_reset_closure
from server.app.services.workflow_definitions import workspace_revision_definition
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.workflow_worker.hydration_dangling import rewrite_pending
from server.app.workflow_worker.input_hydration import live_probe_names
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.revision_format import definition_hash, serialize_definition
from server.app.workflows.sharding_batch import running_shard_nodes, shard_effective_statuses
from server.app.workflows.start_node import START_NODE_TYPE
from server.app.workflows.workflow_consumption import artifact_consumption_index, artifact_producers

_EMPTY_JOB_DIR = Path(__file__).resolve().with_name("__artifact_dangling_empty_job_dir__")


def _timestamp(value: Any) -> str:
    return str(value.isoformat() if hasattr(value, "isoformat") else value)


def manifest_row_identity(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_key": str(row["node_key"]),
        "storage_key": str(row["storage_key"]),
        "size_bytes": int(row["size_bytes"]),
        "content_hash": str(row.get("content_hash") or ""),
        "uploaded_at": _timestamp(row["uploaded_at"]),
    }


def latest_manifest_groups(
    rows: list[dict[str, Any]],
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Group complete same-name sets using hydration/API's exact latest order."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["name"]), []).append(manifest_row_identity(row))
    result = []
    for name, identities in grouped.items():
        identities.sort(key=lambda row: (row["uploaded_at"], row["node_key"]))
        result.append((name, identities))
    return sorted(result)


def resolve_definition(job: dict[str, Any]) -> tuple[WorkflowDefinition | None, dict[str, Any]]:
    active = job.get("active_revision")
    info: dict[str, Any] = {
        "status": "unavailable",
        "job_revision_id": str(job.get("workflow_revision_id") or ""),
        "job_version": job.get("workflow_version"),
        "job_definition_hash": str(job.get("workflow_definition_hash") or ""),
        "job_snapshot_present": bool(job.get("workflow_definition_snapshot_json")),
        "active_revision_id": str(active.get("id") or "") if active else "",
        "active_definition_hash": str(active.get("definition_hash") or "") if active else "",
    }
    definition = definition_from_job_snapshot(job)
    if definition is not None:
        info.update(
            status="resolved",
            source="job_snapshot",
            resolved_sha256=definition_hash(serialize_definition(definition)),
        )
        return definition, info
    try:
        definition = workspace_revision_definition(active)
    except Exception as exc:
        # #204 broad-except audit: an untrusted persisted active definition can
        # fail JSON/schema validation through several exception families. The
        # affected entries stay report-only; other jobs keep their dry-run plan.
        info["error"] = {"type": type(exc).__name__}
        return None, info
    if definition is None:
        info["error"] = {"type": "NoActiveWorkflowRevision"}
        return None, info
    info.update(
        status="resolved",
        source="active_revision",
        resolved_sha256=definition_hash(serialize_definition(definition)),
    )
    return definition, info


def _rerun_scope(
    definition: WorkflowDefinition | None,
    job: dict[str, Any],
    name: str,
    statuses: dict[str, str],
    live_names: frozenset[str],
) -> dict[str, Any]:
    reasons: list[str] = []
    if definition is None:
        return {
            "scope_eligible": False,
            "actionable": False,
            "seed_node": "",
            "seed_nodes": [],
            "reset_closure": [],
            "staging_output_names": [],
            "reasons": ["definition_unavailable"],
        }
    consumers = artifact_consumption_index(definition).get(name, frozenset())
    scope = defer_scope(definition, job)
    in_live = name in live_names
    in_scope = bool(consumers & scope)
    if not in_live:
        reasons.append("not_live_probe")
    if not in_scope:
        reasons.append("outside_run_to_scope")
    if rewrite_pending(definition, statuses, name):
        reasons.append("in_flight_producer_will_rewrite")
    running_nodes = sorted(key for key, status in statuses.items() if status == "running")
    raw_running = sorted(
        str(node["node_key"]) for node in job["nodes"] if str(node["status"]) == "running"
    )
    if raw_running:
        reasons.append("job_has_running_nodes")
    producers = artifact_producers(definition).get(name, set())
    seeds = sorted(
        key for key in producers & scope if definition.nodes[key].node_type != START_NODE_TYPE
    )
    if not producers:
        reasons.append("no_current_producer")
    elif not seeds:
        reasons.append("no_executable_producer_in_run_to_scope")
    closure: set[str] = set()
    staged: set[str] = set()
    if seeds:
        closure = rerun_reset_closure(definition, [seeds[0]])
        staged = staging_output_names(definition, closure)
        if closure - scope:
            reasons.append("reset_closure_outside_run_to_scope")
        if name not in staged:
            reasons.append("rmw_or_staging_exclusion")
    return {
        "scope_eligible": not reasons,
        "actionable": False,
        "seed_node": seeds[0] if seeds else "",
        "seed_nodes": seeds,
        "reset_closure": sorted(closure),
        "reset_closure_outside_run_to_scope": sorted(closure - scope),
        "staging_output_names": sorted(staged),
        "reasons": reasons,
        "live": {
            "in_live_probe": in_live,
            "in_run_to_scope": in_scope,
            "rewrite_pending": rewrite_pending(definition, statuses, name),
            "effective_running_nodes": running_nodes,
            "raw_running_nodes": raw_running,
        },
    }


def job_state_projection(job: dict[str, Any]) -> dict[str, Any]:
    """Raw/effective node state and the exact live-probe basis for one job."""
    definition, _ = resolve_definition(job)
    raw_statuses = {str(node["node_key"]): str(node["status"]) for node in job["nodes"]}
    statuses = dict(raw_statuses)
    if definition is not None:
        running = running_shard_nodes(definition, raw_statuses)
        pending = set(job["pending_shard_nodes"]) & set(running)
        statuses = shard_effective_statuses(raw_statuses, pending)
    return {
        "status": str(job["status"]),
        "execution_generation": int(job["execution_generation"]),
        "execution_mode": str(job.get("execution_mode") or "full"),
        "target_node_key": job.get("target_node_key"),
        "storage_dir": str(job.get("storage_dir") or ""),
        "node_statuses": dict(sorted(raw_statuses.items())),
        "effective_node_statuses": dict(sorted(statuses.items())),
        "pending_shard_nodes": sorted(str(key) for key in job["pending_shard_nodes"]),
    }


def job_entries(job: dict[str, Any]) -> list[dict[str, Any]]:
    definition, definition_info = resolve_definition(job)
    statuses = dict(job_state_projection(job)["effective_node_statuses"])
    live_names: frozenset[str] = frozenset()
    if definition is not None:
        live_names = live_probe_names(definition, statuses, _EMPTY_JOB_DIR)
    groups = latest_manifest_groups(job["artifacts"])
    entries = []
    for name, rows in groups:
        latest = rows[-1]
        producer_statuses = {}
        if definition is not None:
            producer_statuses = {
                key: statuses.get(key, "pending")
                for key in sorted(artifact_producers(definition).get(name, set()))
            }
        rerun = _rerun_scope(definition, job, name, statuses, live_names)
        retire_names = set(rerun["staging_output_names"])
        rerun["retire_manifest"] = [
            {"name": retire_name, "manifest_rows": retire_rows}
            for retire_name, retire_rows in groups
            if retire_name in retire_names
        ]
        entries.append(
            {
                "workspace_id": str(job["workspace_id"]),
                "job_id": str(job["id"]),
                "job_status": str(job["status"]),
                "execution_generation": int(job["execution_generation"]),
                "job_state": job_state_projection(job),
                "name": name,
                "definition": definition_info,
                "latest_row": latest,
                "manifest_rows": rows,
                "node": {
                    "latest_row_node_key": latest["node_key"],
                    "latest_row_status": statuses.get(latest["node_key"], "missing"),
                    "producer_statuses": producer_statuses,
                },
                "rerun": rerun,
            }
        )
    return entries


def entry_identity(entry: dict[str, Any]) -> dict[str, Any]:
    """Plan pins revalidated after every lock-free probe and inside the mutation."""
    return {
        key: entry[key]
        for key in (
            "workspace_id",
            "job_id",
            "job_status",
            "execution_generation",
            "job_state",
            "name",
            "definition",
            "latest_row",
            "manifest_rows",
            "node",
        )
    } | {
        "rerun": {
            key: value
            for key, value in entry["rerun"].items()
            if key not in {"health_gate_passed", "actionable"}
        }
    }


@dataclass
class RecoveryPlanGuard:
    queries: Any
    job_id: str
    entries: list[dict[str, Any]]
    seed_nodes: tuple[str, ...]
    affected_nodes: tuple[str, ...]
    staged_names: frozenset[str]
    retire_manifest: dict[str, list[dict[str, Any]]]
    definition_json: str
    retired_rows: list[dict[str, Any]] = field(default_factory=list, init=False)

    @staticmethod
    def _stale(detail: str) -> NoReturn:
        raise JobMutationConflict("stale_plan", f"Artifact recovery plan is stale: {detail}")

    def validate(
        self,
        conn: Any,
        *,
        definition: WorkflowDefinition,
        affected_nodes: list[str],
        staged_names: frozenset[str],
    ) -> None:
        current = self.queries.artifact_dangling_job_in_transaction(conn, self.job_id)
        if current is None:
            self._stale("job disappeared")
        locked_definition, _ = resolve_definition(current)
        if (
            locked_definition is None
            or serialize_definition(locked_definition) != self.definition_json
        ):
            self._stale("workflow definition changed")
        if serialize_definition(definition) != self.definition_json:
            self._stale("rerun definition differs from the locked definition")
        if tuple(affected_nodes) != self.affected_nodes or staged_names != self.staged_names:
            self._stale("rerun closure or staging scope changed")
        current_entries = {entry["name"]: entry_identity(entry) for entry in job_entries(current)}
        for expected in self.entries:
            if current_entries.get(expected["name"]) != entry_identity(expected):
                self._stale(f"job/artifact pins changed for {expected['name']}")
        groups = dict(latest_manifest_groups(current["artifacts"]))
        actual_retire = {name: groups.get(name, []) for name in self.staged_names}
        if actual_retire != self.retire_manifest:
            self._stale("complete retire manifest changed")

    def validate_retired_rows(self, conn: Any, rows: list[dict[str, Any]]) -> None:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row["name"]), []).append(
                {"node_key": str(row["node_key"]), "storage_key": str(row["storage_key"])}
            )
        for identities in grouped.values():
            identities.sort(key=lambda row: row["node_key"])
        expected = {
            name: sorted(
                (
                    {"node_key": row["node_key"], "storage_key": row["storage_key"]}
                    for row in identities
                ),
                key=lambda row: row["node_key"],
            )
            for name, identities in self.retire_manifest.items()
        }
        actual = {name: grouped.get(name, []) for name in self.staged_names}
        if set(grouped) - set(self.staged_names) or actual != expected:
            self._stale("rows changed before the retire statement")
        from server.app.services.job_rerun.recovery_final import validate_final_recovery

        validate_final_recovery(self, conn)
        self.retired_rows = list(rows)
