from collections.abc import Callable
from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries
from server.app.storage_paths import ManagedPathError, make_data_relative, resolve_job_dir
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.pi_runner import PiRunner
from server.app.workflows.scheduler import (
    _node_statuses,
    _refresh_job_status,
    find_ready_nodes,
)
from server.app.workflows.skill_version import resolve_skill_version
from server.app.workflows.skills import resolve_workflow_skill

# isort: off
# fmt: off
from server.app.workflows.question_comprehension_info import (
    assemble_comprehension_info as qci_assemble_comprehension_info,
    classify_comprehension_eligibility as qci_classify_comprehension_eligibility,
    clean_and_parse as qci_clean_and_parse,
    fetch_questions as qci_fetch_questions,
    finalize_non_uploadable as qci_finalize_non_uploadable,
)
# fmt: on
# isort: on

LocalHandler = Callable[[dict[str, Any], Path, dict[str, Any] | None], None]
LOCAL_HANDLERS: dict[str, dict[str, LocalHandler]] = {
    "question_comprehension_info": {
        "fetch_questions": qci_fetch_questions,
        "clean_and_parse": qci_clean_and_parse,
        "classify_comprehension_eligibility": qci_classify_comprehension_eligibility,
        "finalize_non_uploadable": qci_finalize_non_uploadable,
        "assemble_comprehension_info": qci_assemble_comprehension_info,
    },
}


def _resolve_job_dir(job: dict[str, Any], jobs_dir: Path | None) -> Path:
    if jobs_dir is None:
        raise ManagedPathError(
            "jobs_dir managed root is required",
            record_id=str(job["id"]),
            root_kind="job",
        )
    return resolve_job_dir(job, jobs_dir)


def execute_local_node_once(
    job_db: JobQueries,
    definition: WorkflowDefinition,
    job: dict[str, Any],
    node_key: str,
    logs_dir: Path,
    settings_config: dict[str, Any] | None = None,
    jobs_dir: Path | None = None,
) -> bool:
    handler = LOCAL_HANDLERS.get(definition.key, {}).get(node_key)
    if handler is None:
        raise ValueError(f"No local handler for {definition.key}.{node_key}")

    log_path = logs_dir / "jobs" / f"{job['id']}-{node_key}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    run = job_db.start_node_run(
        job["id"], node_key, ["local", node_key], make_data_relative(log_path, logs_dir.parent)
    )
    if run is None:
        return False

    try:
        handler(
            job,
            _resolve_job_dir(job, jobs_dir),
            {"job_db": job_db, "settings_config": settings_config or {}},
        )
    except Exception as exc:
        error_message = str(exc)
        log_path.write_text(error_message, encoding="utf-8")
        job_db.finish_node_run(run["id"], "failed", 1, error_message)
        return False

    log_path.write_text("completed\n", encoding="utf-8")
    job_db.finish_node_run(run["id"], "completed", 0, "")
    return True


def execute_node_once(
    job_db: JobQueries,
    definition: WorkflowDefinition,
    job: dict[str, Any],
    node_key: str,
    logs_dir: Path,
    settings_config: dict[str, Any] | None = None,
    pi_runner: PiRunner | None = None,
    skill_root: Path | None = None,
    jobs_dir: Path | None = None,
) -> bool:
    if LOCAL_HANDLERS.get(definition.key, {}).get(node_key) is not None:
        return execute_local_node_once(
            job_db,
            definition,
            job,
            node_key,
            logs_dir,
            settings_config=settings_config,
            jobs_dir=jobs_dir,
        )
    if pi_runner is None or skill_root is None:
        raise ValueError("Pi runner is not configured")
    node = definition.nodes[node_key]
    skill_dir = resolve_workflow_skill(skill_root, f"{definition.key}/{node.capability}")
    skill_version = resolve_skill_version(skill_dir)
    result = pi_runner.run(
        job=job,
        node_key=node_key,
        skill_dir=skill_dir,
        inputs=node.inputs,
        outputs=node.outputs,
        tools=["read", "write", "bash"],
        job_db=job_db,
        job_dir=_resolve_job_dir(job, jobs_dir),
        skill_version=skill_version,
    )
    return result.status == "completed"


def _execute_node_wrapped(
    job_db: JobQueries,
    definition: WorkflowDefinition,
    job: dict[str, Any],
    node_key: str,
    logs_dir: Path,
    settings_config: dict[str, Any] | None = None,
    pi_runner: PiRunner | None = None,
    skill_root: Path | None = None,
    jobs_dir: Path | None = None,
) -> bool:
    try:
        return execute_node_once(
            job_db,
            definition,
            job,
            node_key,
            logs_dir,
            settings_config=settings_config,
            pi_runner=pi_runner,
            skill_root=skill_root,
            jobs_dir=jobs_dir,
        )
    except Exception as exc:
        error_message = str(exc)
        runs = job_db.list_node_runs(job["id"])
        latest_run = None
        for run in reversed(runs):
            if run["node_key"] == node_key and run["status"] == "running":
                latest_run = run
                break
        if latest_run is not None:
            job_db.finish_node_run(latest_run["id"], "failed", 1, error_message)
        else:
            job_db.update_job_node(
                job["id"], node_key, status="failed", error_message=error_message
            )
        job_db.update_job_status(job["id"], "failed", error_message)
        return False


def process_ready_workflow_node(
    job_db: JobQueries,
    definition: WorkflowDefinition,
    logs_dir: Path,
    settings_config: dict[str, Any] | None = None,
    jobs_dir: Path | None = None,
) -> bool:
    local_handler_keys = set(LOCAL_HANDLERS.get(definition.key, {}))
    for job in job_db.list_jobs(workspace_id=None, workflow_key=definition.key):
        if job.get("status") in ("completed", "failed"):
            continue
        statuses = _node_statuses(job_db, job["id"])
        ready_nodes = find_ready_nodes(definition, statuses, _resolve_job_dir(job, jobs_dir))
        local_ready_nodes = [node for node in ready_nodes if node.key in local_handler_keys]
        if not local_ready_nodes:
            _refresh_job_status(job_db, job["id"])
            continue

        node = local_ready_nodes[0]
        try:
            processed = execute_node_once(
                job_db,
                definition,
                job,
                node.key,
                logs_dir,
                settings_config=settings_config,
                jobs_dir=jobs_dir,
            )
        except Exception as exc:
            error_message = str(exc)
            job_db.update_job_node(
                job["id"],
                node.key,
                status="failed",
                error_message=error_message,
            )
            job_db.update_job_status(job["id"], "failed", error_message)
            return True
        _refresh_job_status(job_db, job["id"])
        return processed
    return False
