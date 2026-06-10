from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries
from server.app.pipelines.definition import PipelineDefinition
from server.app.pipelines.executor import execute_node_once
from server.app.pipelines.pi_runner import PiRunner
from server.app.pipelines.registry import list_registered_pipelines
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


def _execute_node_wrapped(
    job_db: JobQueries,
    definition: PipelineDefinition,
    job: dict[str, Any],
    node_key: str,
    logs_dir: Path,
    settings_config: dict[str, Any] | None,
    pi_runner: PiRunner | None,
    skill_root: Path | None,
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
        )
    except Exception as exc:
        error_message = str(exc)
        logger.exception("pipeline node %s.%s failed", job["id"], node_key)
        # If a run exists for this node, finish it; otherwise update the node directly.
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


def process_ready_pipeline_node(
    job_db: JobQueries,
    definition: PipelineDefinition,
    logs_dir: Path,
    settings_config: dict[str, Any] | None = None,
) -> bool:
    for job in job_db.list_jobs(workspace_id=None, pipeline_key=definition.key):
        statuses = _node_statuses(job_db, job["id"])
        ready_nodes = find_ready_nodes(definition, statuses, Path(str(job["storage_dir"])))
        local_ready_nodes = [node for node in ready_nodes if node.runner == "local"]
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


class PipelineWorkerThread:
    def __init__(self, job_db: JobQueries, settings: Settings):
        self.job_db = job_db
        self.settings = settings
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._local_executor: ThreadPoolExecutor | None = None
        self._agent_executor: ThreadPoolExecutor | None = None
        self._futures: dict[tuple[str, str], Future[bool]] = {}
        self._definitions: list[PipelineDefinition] = []
        self._pi_runner: PiRunner | None = None
        self._skill_root: Path | None = None

    def start(self) -> None:
        self._definitions = list_registered_pipelines(self.settings.root_dir)
        max_local = max((d.concurrency.local for d in self._definitions), default=1)
        max_agent = max((d.concurrency.agent for d in self._definitions), default=1)
        self._local_executor = ThreadPoolExecutor(max_workers=max_local)
        self._agent_executor = ThreadPoolExecutor(max_workers=max_agent)
        pi_raw = self.settings.config.get("pipelines", {}).get("pi", {})
        if isinstance(pi_raw, dict):
            self._skill_root = self.settings.root_dir / "server" / "app" / "pipelines" / "skills"
            self._pi_runner = PiRunner.from_config(pi_raw, self._skill_root)

        def _loop() -> None:
            while not self.stop_event.is_set():
                try:
                    processed = self._poll()
                except Exception:
                    logger.exception("pipeline worker poll failed")
                    processed = False
                self.stop_event.wait(0.2 if processed else 3)

        self._thread = threading.Thread(target=_loop, name="pipeline-worker", daemon=True)
        self._thread.start()

    def _poll(self) -> bool:
        if not self._definitions:
            return False

        # Reap completed futures
        for key in list(self._futures):
            future = self._futures[key]
            if future.done():
                job_id, node_key = key
                try:
                    future.result()
                except Exception:
                    logger.exception("pipeline future %s.%s failed", job_id, node_key)
                _refresh_job_status(self.job_db, job_id)
                del self._futures[key]

        # Submit new ready nodes across all registered pipelines
        processed = False
        for definition in self._definitions:
            for job in self.job_db.list_jobs(workspace_id=None, pipeline_key=definition.key):
                statuses = _node_statuses(self.job_db, job["id"])
                ready_nodes = find_ready_nodes(definition, statuses, Path(str(job["storage_dir"])))
                if not ready_nodes:
                    _refresh_job_status(self.job_db, job["id"])
                    continue

                for node in ready_nodes:
                    key = (job["id"], node.key)
                    if key in self._futures:
                        continue

                    if node.runner == "local":
                        assert self._local_executor is not None
                        self._futures[key] = self._local_executor.submit(
                            _execute_node_wrapped,
                            self.job_db,
                            definition,
                            job,
                            node.key,
                            self.settings.logs_dir,
                            self.settings.config,
                            None,
                            None,
                        )
                        processed = True
                    elif (
                        node.agent is not None
                        and node.agent.engine == "pi"
                        and self._pi_runner is not None
                        and self._skill_root is not None
                    ):
                        assert self._agent_executor is not None
                        self._futures[key] = self._agent_executor.submit(
                            _execute_node_wrapped,
                            self.job_db,
                            definition,
                            job,
                            node.key,
                            self.settings.logs_dir,
                            self.settings.config,
                            self._pi_runner,
                            self._skill_root,
                        )
                        processed = True

        return processed

    def stop(self, timeout: float = 3) -> None:
        self.stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        if self._local_executor is not None:
            self._local_executor.shutdown(wait=True, cancel_futures=False)
        if self._agent_executor is not None:
            self._agent_executor.shutdown(wait=True, cancel_futures=False)
