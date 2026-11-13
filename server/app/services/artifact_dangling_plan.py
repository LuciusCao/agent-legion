"""Deterministic dry-run recovery plan for dangling job artifacts (#1019).

This module never mutates jobs or objects. Apply requires schema v2 evidence,
explicit threshold calibration, and a fresh plan identical to the reviewed one.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from typing import Any, Protocol

from server.app.services.artifact_dangling_probe import (
    DANGLING_CANDIDATE_OUTCOMES,
    OK,
    UNKNOWN,
    ArtifactDanglingStorage,
    ArtifactHashLimits,
    probe_artifact,
    probe_storage,
)
from server.app.services.job_rerun.recovery_plan import job_entries

DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_CANDIDATES = 20
DEFAULT_MAX_CANDIDATE_RATIO = 0.02


class ArtifactDanglingQueries(Protocol):
    def artifact_dangling_job_page(
        self,
        *,
        after: tuple[Any, str] | None = None,
        limit: int = 100,
        workspace_ids: Sequence[str] | None = None,
        job_ids: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]: ...


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
    thresholds_calibrated: bool = False,
    hash_limits: ArtifactHashLimits | None = None,
    configuration: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Scan, prove, and return a stable machine-readable dry-run plan."""
    if max_candidates < 0 or not 0 <= max_candidate_ratio <= 1:
        raise ValueError("invalid artifact dangling report thresholds")
    budget = hash_limits or ArtifactHashLimits()
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
            entries.extend(job_entries(job))
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
        entry["probe"] = probe_artifact(storage, entry["latest_row"], storage_probe, limits=budget)

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
        "schema_version": 2,
        "mode": "dry-run-only",
        "recovery": {
            "apply_implemented": True,
            "rerun_executed": False,
            "manifest_rows_deleted": 0,
            "object_bytes_written": 0,
            "hydration_notices_cleared": False,
            "scheduling_resumed": False,
            "production_apply_allowed": thresholds_calibrated,
            "next_step": (
                "review plan_sha256 and apply only in an authorized maintenance window"
                if thresholds_calibrated
                else "explicitly calibrate thresholds before apply"
            ),
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
                "development_defaults_only": not thresholds_calibrated,
                "calibrated": thresholds_calibrated,
                "production_apply_allowed": thresholds_calibrated,
            },
            "hash_limits": budget.evidence(),
        },
        "positive_controls": [used_controls[key] for key in sorted(used_controls)],
        "artifacts": report_entries,
    }
    plan["plan_sha256"] = artifact_dangling_plan_sha256(plan)
    return plan
