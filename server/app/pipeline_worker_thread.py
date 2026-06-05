from __future__ import annotations

import logging
import threading
from pathlib import Path

from server.app.jobs import JobQueries
from server.app.pipelines.definition import PipelineDefinition, load_pipeline_definition
from server.app.pipelines.executor import execute_node_once
from server.app.pipelines.scheduler import find_ready_nodes, summarize_job_status
from server.app.settings import Settings

logger = logging.getLogger(__name__)


def _node_statuses(job_db: JobQueries, job_id: str) -> dict[str, str]:
    return {node["node_key"]: node["status"] for node in job_db.list_job_nodes(job_id)}


def _refresh_job_status(job_db: JobQueries, job_id: str) -> None:
    nodes = job_db.list_job_nodes(job_id)
    status = summarize_job_status([node["status"] for node in nodes])
    error_message = ""
    if status == "failed":
        error_message = next(
            (str(node["error_message"]) for node in nodes if node.get("error_message")),
            "",
        )
    job_db.update_job_status(job_id, status, error_message)


def process_ready_pipeline_node(
    job_db: JobQueries,
    definition: PipelineDefinition,
    logs_dir: Path,
) -> bool:
    for job in job_db.list_jobs(pipeline_key=definition.key):
        statuses = _node_statuses(job_db, job["id"])
        ready_nodes = find_ready_nodes(definition, statuses, Path(str(job["storage_dir"])))
        local_ready_nodes = [node for node in ready_nodes if node.runner == "local"]
        if not local_ready_nodes:
            _refresh_job_status(job_db, job["id"])
            continue

        node = local_ready_nodes[0]
        try:
            processed = execute_node_once(job_db, definition, job, node.key, logs_dir)
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


class PipelineWorkerThread:
    def __init__(self, job_db: JobQueries, settings: Settings):
        self.job_db = job_db
        self.settings = settings
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        definition = load_pipeline_definition(
            self.settings.root_dir / "config" / "pipelines" / "question_content.yaml"
        )

        def _loop() -> None:
            while not self.stop_event.is_set():
                try:
                    processed = process_ready_pipeline_node(
                        self.job_db,
                        definition,
                        self.settings.logs_dir,
                    )
                except Exception:
                    logger.exception("pipeline worker poll failed")
                    processed = False
                self.stop_event.wait(0.2 if processed else 3)

        self._thread = threading.Thread(target=_loop, name="pipeline-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3) -> None:
        self.stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
