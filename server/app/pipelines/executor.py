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
    if node.agent is None:
        raise ValueError(f"Node {definition.key}.{node_key} has no agent configuration")
    if node.agent.engine != "pi":
        raise ValueError(
            f"Unsupported agent engine {node.agent.engine!r} for {definition.key}.{node_key}"
        )

    skill_dir = resolve_pipeline_skill(skill_root, node.agent.skill)
    result = pi_runner.run(
        job=job,
        node_key=node_key,
        skill_dir=skill_dir,
        inputs=node.inputs,
        outputs=node.outputs,
        tools=node.agent.tools,
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
    node = definition.nodes[node_key]
    if node.runner == "local":
        return execute_local_node_once(
            job_db,
            definition,
            job,
            node_key,
            logs_dir,
            settings_config=settings_config,
        )
    if node.agent is not None and node.agent.engine == "pi":
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
    raise ValueError(f"Unsupported runner for {definition.key}.{node_key}")
