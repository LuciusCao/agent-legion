from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries
from server.app.pipelines.definition import PipelineDefinition
from server.app.pipelines.question_content import fetch_question_context

LocalHandler = Callable[[dict[str, Any], Path, dict[str, Any]], None]

LOCAL_HANDLERS: dict[str, dict[str, LocalHandler]] = {
    "question_content": {
        "fetch_question_context": fetch_question_context,
    }
}


def execute_node_once(
    job_db: JobQueries,
    definition: PipelineDefinition,
    job: dict[str, Any],
    node_key: str,
    logs_dir: Path,
    settings_config: dict[str, Any] | None = None,
) -> bool:
    node = definition.nodes[node_key]
    if node.runner != "local":
        return False

    handler = LOCAL_HANDLERS.get(definition.key, {}).get(node_key)
    if handler is None:
        raise ValueError(f"No local handler for {definition.key}.{node_key}")

    log_path = logs_dir / "jobs" / f"{job['id']}-{node_key}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    run = job_db.start_node_run(job["id"], node_key, ["local", node_key], str(log_path))

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
        return True

    log_path.write_text("completed\n", encoding="utf-8")
    job_db.finish_node_run(run["id"], "completed", 0, "")
    return True
