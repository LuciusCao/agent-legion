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
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        workspace_worker_control: Any | None = None,
        agent_manager: Any | None = None,
        executor_registry: Any | None = None,
    ):
        self.job_db = job_db
        self.settings = settings
        self.workspace_worker_control = workspace_worker_control
        self.agent_manager = agent_manager
        self.executor_registry = executor_registry
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        # Compat aliases — old code/tests may reference these fields
        self._local_executor: ThreadPoolExecutor | None = None
        self._agent_executor: ThreadPoolExecutor | None = None
        self._futures: dict[tuple[str, str], Future[bool]] = {}
        self._local_futures: set[tuple[str, str]] = set()
        self._agent_futures: set[tuple[str, str]] = set()
        self._job_workspace_ids: dict[str, str] = {}
        self._definitions: list[PipelineDefinition] = []
        self._pi_runner: PiRunner | None = None
        self._skill_root: Path | None = None
        self._max_local: int = 1
        self._max_agent: int = 1
        # NEW per-workspace fields
        self._ws_agent_executors: dict[str, ThreadPoolExecutor] = {}
        self._ws_local_executors: dict[str, ThreadPoolExecutor] = {}
        self._ws_agent_futures: dict[str, set[tuple[str, str]]] = {}
        self._ws_local_futures: dict[tuple[str, str], set[tuple[str, str]]] = {}
        self._ws_agent_limits: dict[str, int] = {}

    def _ensure_workspace_executors(self, workspace_id: str) -> None:
        agents = self.job_db.list_workspace_agents(workspace_id)
        pi_agent = next((a for a in agents if a["agent_id"] == "pi"), None)

        if pi_agent is None:
            pi_limit = max((d.concurrency.agent for d in self._definitions), default=1)
            self.job_db.upsert_workspace_agent_assignment(workspace_id, "pi", pi_limit)
        else:
            pi_limit = pi_agent["concurrency_limit"]

        workspace = self.job_db.get_workspace(workspace_id)
        pipeline_config: dict[str, Any] = {}
        if workspace is not None:
            raw_config = workspace.get("pipeline_config")
            if isinstance(raw_config, dict):
                pipeline_config = raw_config

        local_override = pipeline_config.get("local")
        nodes_override = pipeline_config.get("nodes")
        if not isinstance(nodes_override, dict):
            nodes_override = {}

        local_default = max((d.concurrency.local for d in self._definitions), default=1)
        if isinstance(local_override, int) and local_override >= 1:
            local_default = local_override

        node_limit_sum = sum(
            nodes_override.get(node.key, d.concurrency.nodes.get(node.key, d.concurrency.local))
            for d in self._definitions
            for node in d.nodes.values()
            if node.runner == "local"
        )
        local_limit = max(local_default, node_limit_sum)

        current_local_limit: int | None = None
        if workspace_id in self._ws_local_executors:
            current_local_limit = self._ws_local_executors[workspace_id]._max_workers

        if (
            workspace_id in self._ws_local_executors
            and current_local_limit == local_limit
            and self._ws_agent_limits.get(workspace_id) == pi_limit
        ):
            return

        # If recreating (config changed or old executor shut down), clean up first
        if workspace_id in self._ws_local_executors:
            self._ws_local_executors[workspace_id].shutdown(wait=False, cancel_futures=True)
            self._ws_agent_executors[workspace_id].shutdown(wait=False, cancel_futures=True)

        self._ws_agent_limits[workspace_id] = pi_limit

        self._ws_local_executors[workspace_id] = ThreadPoolExecutor(max_workers=local_limit)
        self._ws_agent_executors[workspace_id] = ThreadPoolExecutor(max_workers=pi_limit)

        if self.agent_manager is not None:
            self.agent_manager.add_pi_agent_for_workspace(workspace_id, pi_limit)

    def start(self) -> None:
        self._definitions = list_registered_pipelines(self.settings.root_dir)
        self._max_local = max((d.concurrency.local for d in self._definitions), default=1)
        self._max_agent = max((d.concurrency.agent for d in self._definitions), default=1)
        pi_raw = self.settings.config.get("pipelines", {}).get("pi", {})
        if isinstance(pi_raw, dict):
            self._skill_root = self.settings.root_dir / "server" / "app" / "pipelines" / "skills"
            try:
                self._pi_runner = PiRunner.from_config(pi_raw, self._skill_root)
            except Exception:
                logger.exception("failed to initialise pi runner")

        for ws in self.job_db.list_workspaces():
            self._ensure_workspace_executors(ws["id"])

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

        # Reap completed futures and cancel pending ones for paused workspaces
        for key in list(self._futures):
            future = self._futures[key]
            job_id, node_key = key
            should_clean = False
            if future.done():
                try:
                    future.result()
                except Exception:
                    logger.exception("pipeline future %s.%s failed", job_id, node_key)
                try:
                    _refresh_job_status(self.job_db, job_id)
                except Exception:
                    logger.exception("failed to refresh job status for %s", job_id)
                should_clean = True
            elif not future.running() and self.workspace_worker_control is not None:
                ws_id = self._job_workspace_ids.get(job_id)
                if ws_id is not None and self.workspace_worker_control.is_paused(ws_id):
                    future.cancel()
                    logger.info(
                        "cancelled pending future %s.%s for paused workspace %s",
                        job_id,
                        node_key,
                        ws_id,
                    )
                    should_clean = True
            if should_clean:
                ws_id = self._job_workspace_ids.get(job_id)
                if ws_id is not None:
                    node_local_key = (ws_id, node_key)
                    if (
                        node_local_key in self._ws_local_futures
                        and key in self._ws_local_futures[node_local_key]
                    ):
                        self._ws_local_futures[node_local_key].discard(key)
                        if not self._ws_local_futures[node_local_key]:
                            self._ws_local_futures.pop(node_local_key, None)
                    if ws_id in self._ws_agent_futures and key in self._ws_agent_futures[ws_id]:
                        self._ws_agent_futures[ws_id].discard(key)
                        if self.agent_manager is not None:
                            try:
                                self.agent_manager.set_idle("pi", workspace_id=ws_id)
                            except Exception:
                                logger.exception("failed to set pi idle")
                else:
                    # Fallback to global compat sets
                    if key in self._local_futures:
                        self._local_futures.discard(key)
                    if key in self._agent_futures:
                        self._agent_futures.discard(key)
                        if self.agent_manager is not None:
                            try:
                                self.agent_manager.set_idle("pi")
                            except Exception:
                                logger.exception("failed to set pi idle")
                self._futures.pop(key, None)
                if not any(k[0] == job_id for k in self._futures):
                    self._job_workspace_ids.pop(job_id, None)

        # Defensive: reconcile orphaned agent-future keys (e.g. after a crash)
        for ws_id, keys in list(self._ws_agent_futures.items()):
            for key in list(keys):
                if key not in self._futures:
                    self._ws_agent_futures[ws_id].discard(key)
                    logger.warning("reconciled orphaned agent future %s.%s", key[0], key[1])
                    if self.agent_manager is not None:
                        try:
                            self.agent_manager.set_idle("pi", workspace_id=ws_id)
                        except Exception:
                            logger.exception("failed to set pi idle during reconciliation")
        for key in list(self._agent_futures):
            if key not in self._futures:
                self._agent_futures.discard(key)
                logger.warning("reconciled orphaned agent future %s.%s", key[0], key[1])
                if self.agent_manager is not None:
                    try:
                        self.agent_manager.set_idle("pi")
                    except Exception:
                        logger.exception("failed to set pi idle during reconciliation")

        # Submit new ready nodes across all registered pipelines
        processed = False
        workspace_paused_cache: dict[str, bool] = {}
        for definition in self._definitions:
            for job in self.job_db.list_jobs(workspace_id=None, pipeline_key=definition.key):
                workspace_id = str(job.get("workspace_id") or "default")
                self._job_workspace_ids[job["id"]] = workspace_id
                self._ensure_workspace_executors(workspace_id)
                if workspace_id not in workspace_paused_cache:
                    if self.workspace_worker_control is not None:
                        workspace_paused_cache[workspace_id] = (
                            self.workspace_worker_control.is_paused(workspace_id)
                        )
                    else:
                        workspace_paused_cache[workspace_id] = False
                if workspace_paused_cache[workspace_id]:
                    continue

                statuses = _node_statuses(self.job_db, job["id"])
                ready_nodes = find_ready_nodes(definition, statuses, Path(str(job["storage_dir"])))
                for node in ready_nodes:
                    key = (job["id"], node.key)
                    if key in self._futures:
                        continue

                    if node.runner == "local":
                        node_limit = definition.concurrency.nodes.get(
                            node.key, definition.concurrency.local
                        )
                        local_futures_for_node = self._ws_local_futures.get(
                            (workspace_id, node.key), set()
                        )
                        if len(local_futures_for_node) >= node_limit:
                            continue
                        total_ws_local = sum(
                            len(s)
                            for (ws, _), s in self._ws_local_futures.items()
                            if ws == workspace_id
                        )
                        if total_ws_local >= self._ws_local_executors[workspace_id]._max_workers:
                            continue
                        self._futures[key] = self._ws_local_executors[workspace_id].submit(
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
                        self._ws_local_futures.setdefault((workspace_id, node.key), set()).add(key)
                        processed = True
                    elif (
                        node.agent is not None
                        and node.agent.engine == "pi"
                        and self._pi_runner is not None
                        and self._skill_root is not None
                    ):
                        limit = self._ws_agent_limits.get(workspace_id, 1)
                        agent_futures_for_ws = self._ws_agent_futures.get(workspace_id, set())
                        if len(agent_futures_for_ws) >= limit:
                            continue
                        self._futures[key] = self._ws_agent_executors[workspace_id].submit(
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
                        self._ws_agent_futures.setdefault(workspace_id, set()).add(key)
                        if self.agent_manager is not None:
                            self.agent_manager.set_busy("pi", job, workspace_id=workspace_id)
                        processed = True
                    elif node.runner == "agent":
                        error_message = "Pi runner is not configured"
                        log_path = self.settings.logs_dir / "jobs" / f"{job['id']}-{node.key}.log"
                        log_path.parent.mkdir(parents=True, exist_ok=True)
                        log_path.write_text(error_message, encoding="utf-8")
                        run = self.job_db.start_node_run(
                            job["id"],
                            node.key,
                            ["agent", node.key],
                            str(log_path),
                        )
                        if run is not None:
                            self.job_db.finish_node_run(run["id"], "failed", 1, error_message)
                        _refresh_job_status(self.job_db, job["id"])
                        processed = True

                # Always refresh job status so that a job whose active nodes have
                # finished (but which still has pending ready nodes that could not
                # be launched due to full concurrency slots) correctly reverts to
                # queued instead of staying stale at running.
                _refresh_job_status(self.job_db, job["id"])
        return processed

    def stop(self, timeout: float = 3) -> None:
        self.stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        if self._local_executor is not None:
            self._local_executor.shutdown(wait=True, cancel_futures=False)
        if self._agent_executor is not None:
            self._agent_executor.shutdown(wait=True, cancel_futures=False)
        for executor in self._ws_local_executors.values():
            executor.shutdown(wait=False, cancel_futures=True)
        for executor in self._ws_agent_executors.values():
            executor.shutdown(wait=False, cancel_futures=True)
        self._ws_local_executors.clear()
        self._ws_agent_executors.clear()
        self._ws_agent_futures.clear()
        self._ws_local_futures.clear()
        self._ws_agent_limits.clear()
