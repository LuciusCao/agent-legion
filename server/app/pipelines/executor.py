from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries
from server.app.pipelines.definition import PipelineDefinition
from server.app.pipelines.pi_runner import PiRunner
from server.app.pipelines.question_content import fetch_question_context
from server.app.pipelines.reading_analysis import (
    clean_and_parse,
    fetch_questions,
    mark_question,
)
from server.app.pipelines.scheduler import (
    _node_statuses,
    _refresh_job_status,
    find_ready_nodes,
)
from server.app.pipelines.skills import resolve_pipeline_skill

LocalHandler = Callable[[dict[str, Any], Path, dict[str, Any] | None], None]

LOCAL_HANDLERS: dict[str, dict[str, LocalHandler]] = {
    "question_content": {
        "fetch_question_context": fetch_question_context,
    },
    "reading_analysis": {
        "fetch_questions": fetch_questions,
        "clean_and_parse": clean_and_parse,
        "mark_question": mark_question,
    },
}


def execute_local_node_once(
    job_db: JobQueries,
    definition: PipelineDefinition,
    job: dict[str, Any],
    node_key: str,
    logs_dir: Path,
    settings_config: dict[str, Any] | None = None,
) -> bool:
    handler = LOCAL_HANDLERS.get(definition.key, {}).get(node_key)
    if handler is None:
        raise ValueError(f"No local handler for {definition.key}.{node_key}")

    log_path = logs_dir / "jobs" / f"{job['id']}-{node_key}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    run = job_db.start_node_run(job["id"], node_key, ["local", node_key], str(log_path))
    if run is None:
        return False

    try:
        handler(
            job,
            Path(str(job["storage_dir"])),
            {
                "job_db": job_db,
                "settings_config": settings_config or {},
            },
        )
    except Exception as exc:
        error_message = str(exc)
        log_path.write_text(error_message, encoding="utf-8")
        job_db.finish_node_run(run["id"], "failed", 1, error_message)
        return False

    log_path.write_text("completed\n", encoding="utf-8")
    job_db.finish_node_run(run["id"], "completed", 0, "")
    return True


def execute_agent_node_once(
    job_db: JobQueries,
    definition: PipelineDefinition,
    job: dict[str, Any],
    node_key: str,
    pi_runner: PiRunner,
    skill_root: Path,
) -> bool:
    node = definition.nodes[node_key]
    skill = f"{definition.key}/{node.capability}"
    skill_dir = resolve_pipeline_skill(skill_root, skill)
    result = pi_runner.run(
        job=job,
        node_key=node_key,
        skill_dir=skill_dir,
        inputs=node.inputs,
        outputs=node.outputs,
        tools=["read", "write", "bash"],
        job_db=job_db,
    )
    return result.status == "completed"


def execute_node_once(
    job_db: JobQueries,
    definition: PipelineDefinition,
    job: dict[str, Any],
    node_key: str,
    logs_dir: Path,
    settings_config: dict[str, Any] | None = None,
    pi_runner: PiRunner | None = None,
    skill_root: Path | None = None,
) -> bool:
    has_local_handler = LOCAL_HANDLERS.get(definition.key, {}).get(node_key) is not None
    if has_local_handler:
        return execute_local_node_once(
            job_db,
            definition,
            job,
            node_key,
            logs_dir,
            settings_config=settings_config,
        )
    if pi_runner is None or skill_root is None:
        raise ValueError("Pi runner is not configured")
    return execute_agent_node_once(
        job_db,
        definition,
        job,
        node_key,
        pi_runner,
        skill_root,
    )


def _execute_node_wrapped(
    job_db: JobQueries,
    definition: PipelineDefinition,
    job: dict[str, Any],
    node_key: str,
    logs_dir: Path,
    settings_config: dict[str, Any] | None = None,
    pi_runner: PiRunner | None = None,
    skill_root: Path | None = None,
) -> bool:
    """Run a single pipeline node and mark the job/node failed on unhandled exceptions."""
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
        )
    except Exception as exc:
        error_message = str(exc)
        runs = job_db.list_node_runs(job["id"])
        latest_run = next(
            (
                run
                for run in reversed(runs)
                if run["node_key"] == node_key and run["status"] == "running"
            ),
            None,
        )
        if latest_run is not None:
            job_db.finish_node_run(latest_run["id"], "failed", 1, error_message)
        else:
            job_db.update_job_node(
                job["id"], node_key, status="failed", error_message=error_message
            )
        job_db.update_job_status(job["id"], "failed", error_message)
        return False


def process_ready_pipeline_node(
    job_db: JobQueries,
    definition: PipelineDefinition,
    logs_dir: Path,
    settings_config: dict[str, Any] | None = None,
) -> bool:
    local_handler_keys = set(LOCAL_HANDLERS.get(definition.key, {}))
    for job in job_db.list_jobs(workspace_id=None, pipeline_key=definition.key):
        if job.get("status") in ("completed", "failed"):
            continue
        statuses = _node_statuses(job_db, job["id"])
        ready_nodes = find_ready_nodes(definition, statuses, Path(str(job["storage_dir"])))
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
