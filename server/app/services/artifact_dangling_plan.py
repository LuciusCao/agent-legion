"""Deterministic dry-run recovery plan for dangling job artifacts (#1019).

This module never deletes rows, writes objects, clears hydration notices, or
resumes scheduling. It pins the evidence an M5 rerun apply would have to
revalidate; ``production_apply_allowed`` stays false until thresholds are
calibrated and that separate implementation exists.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from server.app.services.artifact_dangling_probe import (
    DANGLING_CANDIDATE_OUTCOMES,
    OK,
    UNKNOWN,
    ArtifactDanglingStorage,
    probe_artifact,
    probe_storage,
)
from server.app.services.hydration_defer_board import defer_scope
from server.app.services.job_artifact_staging_scope import staging_output_names
from server.app.services.job_reset_closure import rerun_reset_closure
from server.app.services.workflow_definitions import workspace_revision_definition
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.workflow_worker.hydration_dangling import rewrite_pending
from server.app.workflow_worker.input_hydration import live_probe_names
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.sharding_batch import running_shard_nodes, shard_effective_statuses
from server.app.workflows.start_node import START_NODE_TYPE
from server.app.workflows.workflow_consumption import artifact_consumption_index, artifact_producers

DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_CANDIDATES = 20
DEFAULT_MAX_CANDIDATE_RATIO = 0.02
_EMPTY_JOB_DIR = Path(__file__).resolve().with_name("__artifact_dangling_empty_job_dir__")


class ArtifactDanglingQueries(Protocol):
    def artifact_dangling_job_page(
        self,
        *,
        after: tuple[Any, str] | None = None,
        limit: int = 100,
        workspace_ids: Sequence[str] | None = None,
        job_ids: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]: ...


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
    rows: Sequence[dict[str, Any]],
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


def _resolve_definition(job: dict[str, Any]) -> tuple[WorkflowDefinition | None, dict[str, Any]]:
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
        info.update(status="resolved", source="job_snapshot")
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
    info.update(status="resolved", source="active_revision")
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
    rewriting = rewrite_pending(definition, statuses, name)
    if rewriting:
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
            "rewrite_pending": rewriting,
            "effective_running_nodes": running_nodes,
            "raw_running_nodes": raw_running,
        },
    }


def _job_entries(job: dict[str, Any]) -> list[dict[str, Any]]:
    definition, definition_info = _resolve_definition(job)
    raw_statuses = {str(node["node_key"]): str(node["status"]) for node in job["nodes"]}
    statuses = dict(raw_statuses)
    live_names: frozenset[str] = frozenset()
    if definition is not None:
        running = running_shard_nodes(definition, raw_statuses)
        pending = set(job["pending_shard_nodes"]) & set(running)
        statuses = shard_effective_statuses(raw_statuses, pending)
        live_names = live_probe_names(definition, statuses, _EMPTY_JOB_DIR)
    entries = []
    for name, rows in latest_manifest_groups(job["artifacts"]):
        latest = rows[-1]
        producer_statuses = {}
        if definition is not None:
            producer_statuses = {
                key: statuses.get(key, "pending")
                for key in sorted(artifact_producers(definition).get(name, set()))
            }
        entries.append(
            {
                "workspace_id": str(job["workspace_id"]),
                "job_id": str(job["id"]),
                "job_status": str(job["status"]),
                "execution_generation": int(job["execution_generation"]),
                "name": name,
                "definition": definition_info,
                "latest_row": latest,
                "manifest_rows": rows,
                "node": {
                    "latest_row_node_key": latest["node_key"],
                    "latest_row_status": statuses.get(latest["node_key"], "missing"),
                    "producer_statuses": producer_statuses,
                },
                "rerun": _rerun_scope(definition, job, name, statuses, live_names),
            }
        )
    return entries


def _check(code: str, passed: bool, evidence: dict[str, Any]) -> dict[str, Any]:
    return {"code": code, "status": OK if passed else "failed", "passed": passed, **evidence}


def _control(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "workspace_id": entry["workspace_id"],
        "job_id": entry["job_id"],
        "name": entry["name"],
        "latest_row": entry["latest_row"],
        "probe": entry["probe"],
    }


def _first_two_distinct_controls(
    healthy_controls: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """First global control and first from another job, with one bounded scan."""
    if not healthy_controls:
        return None, None
    first = healthy_controls[0]
    first_job_id = first["job_id"]
    alternate = next(
        (
            control
            for index, control in enumerate(healthy_controls)
            if index and control["job_id"] != first_job_id
        ),
        None,
    )
    return first, alternate


def _control_ref(control: dict[str, Any]) -> dict[str, str]:
    return {
        "workspace_id": control["workspace_id"],
        "job_id": control["job_id"],
        "name": control["name"],
        "storage_key": control["latest_row"]["storage_key"],
        "content_sha256": control["probe"]["get"]["content_sha256"],
    }


def artifact_dangling_plan_sha256(plan: dict[str, Any]) -> str:
    payload = {key: value for key, value in plan.items() if key != "plan_sha256"}
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def render_artifact_dangling_plan(plan: dict[str, Any]) -> str:
    if plan.get("plan_sha256") != artifact_dangling_plan_sha256(plan):
        raise ValueError("artifact dangling plan hash does not match its payload")
    return json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def build_artifact_dangling_plan(
    queries: ArtifactDanglingQueries,
    storage: ArtifactDanglingStorage,
    *,
    workspace_ids: Sequence[str] | None = None,
    job_ids: Sequence[str] | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    max_candidate_ratio: float = DEFAULT_MAX_CANDIDATE_RATIO,
    configuration: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Scan, prove, and return a stable machine-readable dry-run plan."""
    if max_candidates < 0 or not 0 <= max_candidate_ratio <= 1:
        raise ValueError("invalid artifact dangling report thresholds")
    workspaces = sorted(set(workspace_ids)) if workspace_ids is not None else None
    jobs_filter = sorted(set(job_ids)) if job_ids is not None else None
    entries: list[dict[str, Any]] = []
    page_evidence: list[dict[str, Any]] = []
    after = None
    job_count = 0
    manifest_count = 0
    while True:
        page = queries.artifact_dangling_job_page(
            after=after,
            limit=page_size,
            workspace_ids=workspaces,
            job_ids=jobs_filter,
        )
        if not page:
            break
        job_count += len(page)
        manifest_count += sum(len(job["artifacts"]) for job in page)
        for job in page:
            entries.extend(_job_entries(job))
        page_evidence.append(
            {
                "page": len(page_evidence) + 1,
                "job_count": len(page),
                "first_job_id": str(page[0]["id"]),
                "last_job_id": str(page[-1]["id"]),
            }
        )
        if len(page) < page_size:
            break
        after = (page[-1]["created_at"], str(page[-1]["id"]))

    entries.sort(key=lambda entry: (entry["workspace_id"], entry["job_id"], entry["name"]))
    referenced_keys = {row["storage_key"] for entry in entries for row in entry["manifest_rows"]}
    storage_probe = probe_storage(storage, referenced_keys, configuration=configuration)
    for entry in entries:
        entry["probe"] = probe_artifact(storage, entry["latest_row"], storage_probe)

    outcome_counts = Counter(entry["probe"]["outcome"] for entry in entries)
    candidate_count = sum(outcome_counts[outcome] for outcome in DANGLING_CANDIDATE_OUTCOMES)
    unknown_count = outcome_counts[UNKNOWN]
    latest_count = len(entries)
    candidate_ratio = candidate_count / latest_count if latest_count else 0.0
    healthy_controls = sorted(
        (
            entry
            for entry in entries
            if entry["probe"]["outcome"] == OK
            and entry["latest_row"]["content_hash"]
            and entry["probe"]["get"].get("content_sha256") == entry["latest_row"]["content_hash"]
        ),
        key=lambda entry: (entry["workspace_id"], entry["job_id"], entry["name"]),
    )
    first_control, alternate_control = _first_two_distinct_controls(healthy_controls)
    used_controls: dict[tuple[str, str, str], dict[str, Any]] = {}
    for entry in entries:
        selected = (
            first_control
            if first_control is not None and entry["job_id"] != first_control["job_id"]
            else alternate_control
        )
        control = _control(selected) if selected is not None else None
        entry["positive_controls"] = [_control_ref(control)] if control is not None else []
        if entry["probe"]["outcome"] in DANGLING_CANDIDATE_OUTCOMES and control is not None:
            key = (control["workspace_id"], control["job_id"], control["name"])
            used_controls[key] = control

    listing = storage_probe.evidence["listing"]
    fingerprint = storage_probe.evidence["configuration"]
    fingerprint_valid = fingerprint.get("algorithm") == "sha256" and all(
        len(str(fingerprint.get(key, ""))) == 64 for key in ("bucket", "endpoint", "configuration")
    )
    candidates = [
        entry for entry in entries if entry["probe"]["outcome"] in DANGLING_CANDIDATE_OUTCOMES
    ]
    positive_control_coverage = sum(bool(entry["positive_controls"]) for entry in candidates)
    checks = [
        _check("configuration_fingerprint", fingerprint_valid, {"algorithm": "sha256"}),
        _check("head_bucket", storage_probe.evidence["head_bucket"]["status"] == OK, {}),
        _check(
            "jobs_list_complete", bool(listing["complete"]), {"page_count": listing["page_count"]}
        ),
        _check(
            "empty_list_with_db_references",
            not (listing["complete"] and listing["key_count"] == 0 and bool(referenced_keys)),
            {"key_count": listing["key_count"], "referenced_key_count": len(referenced_keys)},
        ),
        _check("no_unknown", unknown_count == 0, {"unknown_count": unknown_count}),
        _check(
            "non_candidate_positive_controls",
            positive_control_coverage == candidate_count,
            {"covered_candidates": positive_control_coverage, "candidate_count": candidate_count},
        ),
        _check(
            "candidate_count_threshold",
            candidate_count <= max_candidates,
            {"candidate_count": candidate_count, "maximum": max_candidates},
        ),
        _check(
            "candidate_ratio_threshold",
            candidate_ratio <= max_candidate_ratio,
            {"candidate_ratio": candidate_ratio, "maximum": max_candidate_ratio},
        ),
    ]
    healthy = all(check["passed"] for check in checks)
    for entry in entries:
        entry["rerun"]["health_gate_passed"] = healthy
        entry["rerun"]["actionable"] = bool(
            healthy
            and entry["rerun"]["scope_eligible"]
            and entry["probe"]["outcome"] in DANGLING_CANDIDATE_OUTCOMES
        )
    actionable_count = sum(entry["rerun"]["actionable"] for entry in entries)
    report_entries = [entry for entry in entries if entry["probe"]["outcome"] != OK]
    plan: dict[str, Any] = {
        "report": "job-artifact-dangling-recovery-dry-run",
        "schema_version": 1,
        "mode": "dry-run-only",
        "recovery": {
            "apply_implemented": False,
            "rerun_executed": False,
            "manifest_rows_deleted": 0,
            "object_bytes_written": 0,
            "hydration_notices_cleared": False,
            "scheduling_resumed": False,
            "production_apply_allowed": False,
            "next_step": "M5 constrained rerun after explicit threshold calibration",
        },
        "scan": {
            "ordering": "created_at,id",
            "page_isolation": "repeatable_read_read_only",
            "page_size": page_size,
            "page_count": len(page_evidence),
            "pages": page_evidence,
            "workspace_ids": workspaces,
            "job_ids": jobs_filter,
            "job_count": job_count,
            "latest_row_count": latest_count,
            "manifest_row_count": manifest_count,
            "local_artifact_basis": "synthetic_empty_directory_never_created",
        },
        "storage": storage_probe.evidence,
        "summary": {
            "outcomes": dict(sorted(outcome_counts.items())),
            "candidate_count": candidate_count,
            "candidate_ratio": candidate_ratio,
            "unknown_count": unknown_count,
            "actionable_count": actionable_count,
            "report_entry_count": len(report_entries),
        },
        "health": {
            "passed": healthy,
            "checks": checks,
            "thresholds": {
                "max_candidates": max_candidates,
                "max_candidate_ratio": max_candidate_ratio,
                "development_defaults_only": True,
                "calibrated": False,
                "production_apply_allowed": False,
            },
        },
        "positive_controls": [used_controls[key] for key in sorted(used_controls)],
        "artifacts": report_entries,
    }
    plan["plan_sha256"] = artifact_dangling_plan_sha256(plan)
    return plan
