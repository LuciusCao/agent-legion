"""Constrained apply of a reviewed dangling-artifact dry-run plan (#1019).

A batch is a sequence of independent rerun transactions, never an atomic
multi-job mutation. Planning and all health proofs finish before the first
write; the first stale plan or rerun failure stops later jobs.
"""

from __future__ import annotations

import hmac
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, cast

from server.app.jobs.atomic_mutations import JobMutationConflict
from server.app.services.artifact_dangling_plan import (
    artifact_dangling_plan_sha256,
    build_artifact_dangling_plan,
)
from server.app.services.artifact_dangling_probe import (
    DANGLING_CANDIDATE_OUTCOMES,
    OK,
    ArtifactDanglingStorage,
    ArtifactHashLimits,
    probe_artifact,
    probe_storage,
    s3_configuration_fingerprint,
)
from server.app.services.hydration_defer_board import HYDRATION_DEFER_BOARD
from server.app.services.job_artifact_staging_scope import staging_output_names
from server.app.services.job_operation_error import JobOperationError
from server.app.services.job_rerun.commit import commit_rerun
from server.app.services.job_rerun.recovery_plan import (
    RecoveryPlanGuard,
    entry_identity,
    job_entries,
    latest_manifest_groups,
    resolve_definition,
)
from server.app.services.job_reset_closure import rerun_reset_closure
from server.app.storage.s3_settings import S3Settings
from server.app.workflows.revision_format import serialize_definition

logger = logging.getLogger(__name__)
MAX_APPLY_JOBS = 20


class ArtifactRecoveryError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class PreparedJob:
    workspace_id: str
    job_id: str
    entries: list[dict[str, Any]]


@dataclass(frozen=True)
class PreparedApply:
    reviewed_plan: dict[str, Any]
    jobs: list[PreparedJob]
    hash_limits: ArtifactHashLimits
    configuration: dict[str, str]


def _fail(reason: str, detail: str) -> NoReturn:
    raise ArtifactRecoveryError(reason, detail)


def load_reviewed_plan(path: Path, expected_sha256: str) -> dict[str, Any]:
    """Load local JSON and authenticate both the operator pin and payload hash."""
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ArtifactRecoveryError("invalid_plan", f"Cannot read reviewed plan: {exc}") from exc
    if not isinstance(plan, dict) or not hmac.compare_digest(
        str(plan.get("plan_sha256", "")), expected_sha256.lower()
    ):
        _fail("plan_sha256_mismatch", "Operator plan sha256 does not match the file")
    try:
        embedded_hash = artifact_dangling_plan_sha256(plan)
    except (TypeError, ValueError) as exc:
        raise ArtifactRecoveryError("invalid_plan", "Plan is not canonical JSON") from exc
    if plan.get("plan_sha256") != embedded_hash:
        _fail("invalid_plan", "Plan payload does not match its embedded sha256")
    if (
        plan.get("report") != "job-artifact-dangling-recovery-dry-run"
        or plan.get("schema_version") != 2
        or plan.get("mode") != "dry-run-only"
    ):
        _fail("unsupported_plan", "Apply requires a schema v2 dry-run recovery plan")
    health = plan.get("health")
    recovery = plan.get("recovery")
    if not isinstance(health, dict) or not isinstance(recovery, dict):
        _fail("invalid_plan", "Plan health/recovery sections are missing")
    thresholds = health.get("thresholds")
    if not isinstance(thresholds, dict):
        _fail("invalid_plan", "Plan thresholds are missing")
    if (
        not health.get("passed")
        or not thresholds.get("calibrated")
        or not thresholds.get("production_apply_allowed")
        or not recovery.get("apply_implemented")
        or not recovery.get("production_apply_allowed")
    ):
        _fail("uncalibrated_plan", "Apply requires a healthy, explicitly calibrated plan")
    return cast(dict[str, Any], plan)


def _actionable_jobs(plan: dict[str, Any]) -> list[PreparedJob]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in plan.get("artifacts", []):
        if not entry.get("rerun", {}).get("actionable"):
            continue
        if (
            entry.get("probe", {}).get("outcome") not in DANGLING_CANDIDATE_OUTCOMES
            or not entry["rerun"].get("scope_eligible")
            or entry["rerun"].get("reasons")
            or not entry["rerun"].get("seed_node")
            or entry["name"] not in entry["rerun"].get("staging_output_names", [])
        ):
            _fail("invalid_plan", f"Actionable entry is outside the recovery contract: {entry}")
        grouped.setdefault(str(entry["job_id"]), []).append(entry)
    if len(grouped) > MAX_APPLY_JOBS:
        _fail("batch_too_large", f"Apply is limited to {MAX_APPLY_JOBS} jobs per batch")
    jobs = []
    for job_id, entries in sorted(grouped.items()):
        entries.sort(key=lambda entry: entry["name"])
        names = [entry["name"] for entry in entries]
        if len(names) != len(set(names)) or any(
            str(entry["job_id"]) != job_id for entry in entries
        ):
            _fail("invalid_plan", f"Duplicate or inconsistent job entries for {job_id}")
        jobs.append(PreparedJob(str(entries[0]["workspace_id"]), job_id, entries))
    if sum(len(job.entries) for job in jobs) != int(plan["summary"]["actionable_count"]):
        _fail("invalid_plan", "Actionable summary does not match the plan entries")
    return jobs


def prepare_apply(
    queries: Any,
    storage: ArtifactDanglingStorage,
    reviewed_plan: dict[str, Any],
    settings: S3Settings,
    *,
    expected_bucket: str,
    expected_endpoint: str,
) -> PreparedApply:
    """Reprove configuration and require a fresh plan identical to the review."""
    if settings.bucket != expected_bucket or settings.endpoint_url != expected_endpoint:
        _fail(
            "configuration_mismatch", "Operator bucket/endpoint does not match active S3 settings"
        )
    configuration = s3_configuration_fingerprint(settings)
    if reviewed_plan.get("storage", {}).get("configuration") != configuration:
        _fail("configuration_mismatch", "Reviewed plan belongs to another S3 configuration")
    jobs = _actionable_jobs(reviewed_plan)
    thresholds = reviewed_plan["health"]["thresholds"]
    limits = ArtifactHashLimits.from_evidence(reviewed_plan["health"].get("hash_limits"))
    fresh = build_artifact_dangling_plan(
        queries,
        storage,
        workspace_ids=reviewed_plan["scan"].get("workspace_ids"),
        job_ids=reviewed_plan["scan"].get("job_ids"),
        page_size=int(reviewed_plan["scan"]["page_size"]),
        max_candidates=int(thresholds["max_candidates"]),
        max_candidate_ratio=float(thresholds["max_candidate_ratio"]),
        thresholds_calibrated=True,
        hash_limits=limits,
        configuration=configuration,
    )
    if not fresh["health"]["passed"]:
        _fail("health_gate_failed", "Fresh dry-run health gates failed; no job was applied")
    if fresh["plan_sha256"] != reviewed_plan["plan_sha256"]:
        _fail("stale_plan", "Fresh dry-run evidence differs from the reviewed plan")
    return PreparedApply(reviewed_plan, jobs, limits, configuration)


def _control_map(plan: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    return {
        (str(entry["job_id"]), str(entry["name"]), str(entry["latest_row"]["storage_key"])): entry
        for entry in plan.get("positive_controls", [])
    }


def _probe_job(
    prepared: PreparedApply,
    job: PreparedJob,
    current: dict[str, Any],
    storage: ArtifactDanglingStorage,
) -> None:
    referenced = {str(row["storage_key"]) for row in current["artifacts"]}
    controls = _control_map(prepared.reviewed_plan)
    referenced.update(
        str(entry["latest_row"]["storage_key"])
        for entry in prepared.reviewed_plan["positive_controls"]
    )
    evidence = probe_storage(storage, referenced, configuration=prepared.configuration)
    if (
        evidence.evidence["head_bucket"]["status"] != OK
        or not evidence.evidence["listing"]["complete"]
    ):
        _fail("health_gate_failed", f"Storage health changed before job {job.job_id}")
    for expected in job.entries:
        outcome = probe_artifact(
            storage, expected["latest_row"], evidence, limits=prepared.hash_limits
        )
        if outcome["outcome"] != expected["probe"]["outcome"]:
            _fail("stale_plan", f"Object evidence changed for {job.job_id}/{expected['name']}")
        for reference in expected.get("positive_controls", []):
            key = (
                str(reference["job_id"]),
                str(reference["name"]),
                str(reference["storage_key"]),
            )
            control = controls.get(key)
            if control is None or control["job_id"] == job.job_id:
                _fail("health_gate_failed", "Positive control identity is missing or job-local")
            proved = probe_artifact(
                storage, control["latest_row"], evidence, limits=prepared.hash_limits
            )
            if (
                proved["outcome"] != OK
                or proved["get"].get("content_sha256") != reference["content_sha256"]
            ):
                _fail("health_gate_failed", "Positive control no longer proves GET+hash")


def revalidate_job(
    prepared: PreparedApply,
    job: PreparedJob,
    queries: Any,
    storage: ArtifactDanglingStorage,
) -> tuple[dict[str, Any], Any, RecoveryPlanGuard]:
    """Refresh one job after batch health, then build its lock-invariant guard."""
    rows = queries.artifact_dangling_job_page(job_ids=[job.job_id], limit=2)
    if len(rows) != 1:
        _fail("stale_plan", f"Job disappeared or duplicated: {job.job_id}")
    current = rows[0]
    current_entries = {entry["name"]: entry_identity(entry) for entry in job_entries(current)}
    for expected in job.entries:
        if current_entries.get(expected["name"]) != entry_identity(expected):
            _fail("stale_plan", f"Job state changed for {job.job_id}/{expected['name']}")
    _probe_job(prepared, job, current, storage)
    definition, _ = resolve_definition(current)
    if definition is None:
        _fail("stale_plan", f"Definition is unavailable for {job.job_id}")
    seeds = tuple(sorted({str(entry["rerun"]["seed_node"]) for entry in job.entries}))
    affected = sorted(rerun_reset_closure(definition, seeds))
    staged = frozenset(staging_output_names(definition, set(affected)))
    planned_affected = sorted(
        {key for entry in job.entries for key in entry["rerun"]["reset_closure"]}
    )
    planned_staged = {
        name for entry in job.entries for name in entry["rerun"]["staging_output_names"]
    }
    if affected != planned_affected or staged != planned_staged:
        _fail("stale_plan", f"Combined rerun closure is not additive for {job.job_id}")
    groups = dict(latest_manifest_groups(current["artifacts"]))
    retire_manifest = {name: groups.get(name, []) for name in staged}
    for expected in job.entries:
        for pinned in expected["rerun"]["retire_manifest"]:
            if retire_manifest.get(pinned["name"]) != pinned["manifest_rows"]:
                _fail("stale_plan", f"Retire rows changed for {job.job_id}/{pinned['name']}")
    guard = RecoveryPlanGuard(
        queries=queries,
        job_id=job.job_id,
        entries=job.entries,
        seed_nodes=seeds,
        affected_nodes=tuple(affected),
        staged_names=staged,
        retire_manifest=retire_manifest,
        definition_json=serialize_definition(definition),
    )
    return current, definition, guard


def _job_report(job: PreparedJob, status: str, **extra: Any) -> dict[str, Any]:
    return {
        "workspace_id": job.workspace_id,
        "job_id": job.job_id,
        "names": [entry["name"] for entry in job.entries],
        "status": status,
        **extra,
    }


def execute_apply(
    prepared: PreparedApply,
    rerun_service: Any,
    storage: ArtifactDanglingStorage,
) -> dict[str, Any]:
    """Apply independent jobs in order, stopping at the first conflict/failure."""
    reports: list[dict[str, Any]] = []
    stopped = False
    for index, job in enumerate(prepared.jobs):
        try:
            current, definition, guard = revalidate_job(
                prepared, job, rerun_service.job_db, storage
            )
            result = commit_rerun(
                rerun_service,
                current,
                job.job_id,
                guard.seed_nodes[0],
                definition=definition,
                plan_guard=guard,
            )
            if result["status"] != "succeeded":
                _fail(result["reason_code"] or "rerun_failed", result["message"] or "rerun failed")
        except (ArtifactRecoveryError, JobOperationError, JobMutationConflict) as exc:
            reason = getattr(exc, "reason_code", "stale_plan")
            reports.append(_job_report(job, "not_applied", reason_code=reason, message=str(exc)))
            stopped = True
        except Exception as exc:
            # #204 broad-except audit: the CLI must stop after an unexpected
            # per-job probe/rerun failure and report prior commits honestly.
            # No later job is attempted; the traceback remains in the log.
            logger.exception("Unexpected artifact recovery failure for job %s", job.job_id)
            reports.append(
                _job_report(job, "not_applied", reason_code="recovery_failed", message=str(exc))
            )
            stopped = True
        else:
            HYDRATION_DEFER_BOARD.publish(job.job_id, ())
            reports.append(
                _job_report(
                    job,
                    "applied",
                    seed_nodes=list(guard.seed_nodes),
                    execution_generation=int(current["execution_generation"]) + 1,
                    manifest_rows_retired=len(guard.retired_rows),
                    rerun=result,
                )
            )
        if stopped:
            for remaining in prepared.jobs[index + 1 :]:
                reports.append(
                    _job_report(remaining, "not_attempted", reason_code="stopped_after_failure")
                )
            break
    applied = sum(report["status"] == "applied" for report in reports)
    planned = len(prepared.jobs)
    return {
        "report": "job-artifact-dangling-recovery-apply",
        "schema_version": 1,
        "plan_sha256": prepared.reviewed_plan["plan_sha256"],
        "configuration": prepared.configuration,
        "batch": {
            "max_jobs": MAX_APPLY_JOBS,
            "planned_job_count": planned,
            "atomic": False,
            "stopped_after_failure": stopped,
        },
        "summary": {
            "applied_count": applied,
            "not_applied_count": planned - applied,
            "failed_count": sum(report["status"] == "not_applied" for report in reports),
            "not_attempted_count": sum(report["status"] == "not_attempted" for report in reports),
        },
        "recovery": {
            "rerun_transactions_committed": applied,
            "producers_scheduled_not_completed": True,
            "object_bytes_written_by_apply": 0,
            "hydration_notice_clear": "local_board_and_next_host_worker_poll",
            "scheduler_wakeup": "existing_notify_with_poll_fallback",
        },
        "jobs": reports,
    }


def render_apply_report(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
