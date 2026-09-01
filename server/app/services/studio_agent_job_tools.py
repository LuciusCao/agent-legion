"""Read-only job observation service behind the studio-agent job tools (#329).

Composes the existing job read services (JobQueryService / JobLogService /
JobArtifactService / FailedNodeRunQueryService) — all DB access stays behind
the JobQueries facade inside those services (BOUNDARY-DATA-001); this module
adds the diagnosis-shaped trimming (agent-context-friendly payloads, no local
filesystem paths) and the workspace/session binding checks.

Effecting operations are never exposed here (STUDIO-AGENT-001): failed nodes
surface as ``suggested_actions`` payloads — data the agent quotes back and the
UI turns into a human-confirmed card; the mutation itself always runs through
the host session on the regular job routes.
"""

from __future__ import annotations

from typing import Any

from server.app.jobs import JobQueries
from server.app.services.failed_node_runs import FailedNodeRunQueryService
from server.app.services.job_artifacts import JobArtifactService
from server.app.services.job_errors import NotFoundError
from server.app.services.job_logs import JobLogService
from server.app.services.job_queries import JobQueryService
from server.app.services.workspace_execution_configuration import (
    WorkspaceExecutionConfigurationService,
)
from server.app.settings import Settings

# Agent-context budgets: logs tail (errors cluster at the end), artifacts head
# (structure lives at the top), recent-failure scan per focus node.
_LOG_TAIL_CHARS = 6000
_ARTIFACT_MAX_CHARS = 8000
_RECENT_FAILURES_LIMIT = 5
_LIST_LIMIT_DEFAULT = 20
_LIST_LIMIT_MAX = 100


def _job_brief(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(job["id"]),
        "title": str(job.get("title") or ""),
        "status": str(job["status"]),
        "outcome": str(job.get("outcome") or ""),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }


def _trim_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Trim a JobQueryService._job_summary row to the agent-facing fields
    (drops input/config snapshots and storage paths)."""
    return {
        **_job_brief(summary),
        "error_summary": str(summary.get("error_summary") or ""),
        "active_node_key": summary.get("active_node_key"),
        "completed_nodes": int(summary.get("completed_nodes") or 0),
        "total_nodes": int(summary.get("total_nodes") or 0),
        "is_workflow_outdated": bool(summary.get("is_workflow_outdated")),
        "node_summaries": [
            {
                "node_key": str(node["node_key"]),
                "label": str(node["label"]),
                "status": str(node["status"]),
                "error_message": str(node.get("error_message") or ""),
            }
            for node in summary.get("node_summaries") or []
        ],
    }


def _suggested_actions(job_id: str, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Suggestion payloads for the human-confirmation card — never an effected
    action: the UI executes through the host session on the regular routes."""
    suggestions: list[dict[str, Any]] = []
    for node in nodes:
        if node.get("status") != "failed":
            continue
        node_key = str(node["node_key"])
        suggestions.append(
            {
                "action": "rerun_node",
                "job_id": job_id,
                "node_key": node_key,
                "label": f"重跑节点 {node.get('label') or node_key}",
                "requires_confirmation": True,
            }
        )
    return suggestions


class StudioAgentJobToolsService:
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        object_store: Any | None = None,
    ) -> None:
        self._job_db = job_db
        self._queries = JobQueryService(
            job_db,
            settings,
            WorkspaceExecutionConfigurationService(job_db, settings),
            object_store=object_store,
        )
        self._logs = JobLogService(settings, job_db)
        self._artifacts = JobArtifactService(job_db, object_store=object_store)
        self._failed_runs = FailedNodeRunQueryService(job_db)

    def _job_in_workspace_or_404(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        # The workspace check doubles as the existence check: a job id from
        # another workspace is a 404 (not 403), so ids cannot be probed across
        # workspaces.
        job = self._job_db.get_job(job_id)
        if job is None or str(job["workspace_id"]) != workspace_id:
            raise NotFoundError("Job not found")
        return job

    def _detail_payload(self, job_id: str) -> dict[str, Any]:
        detail = self._queries.detail(job_id)
        nodes = [
            {
                "node_key": str(node["node_key"]),
                "label": str(node.get("label") or node["node_key"]),
                "capability": str(node.get("capability") or ""),
                "status": str(node["status"]),
                "error_message": str(node.get("error_message") or ""),
                "inputs": [str(item) for item in node.get("inputs") or []],
                "outputs": [str(item) for item in node.get("outputs") or []],
                "executor_kind": node.get("executor_kind"),
                "agent_id": node.get("agent_id"),
            }
            for node in detail["nodes"]
        ]
        return {
            "job": _trim_summary(detail["job"]),
            "nodes": nodes,
            # Paths stay server-side (log_path/run_dir are local filesystem
            # locations); the agent gets a has_log flag and fetches content
            # through the logs endpoint.
            "runs": [
                {
                    "id": int(run["id"]),
                    "node_key": str(run.get("node_key") or ""),
                    "status": str(run.get("status") or ""),
                    "error_message": str(run.get("error_message") or ""),
                    "started_at": run.get("started_at"),
                    "finished_at": run.get("finished_at"),
                    "has_log": bool(run.get("log_path")),
                }
                for run in detail["runs"]
            ],
            "artifacts": [str(name) for name in detail["artifacts"]],
            "suggested_actions": _suggested_actions(job_id, detail["nodes"]),
        }

    def list_jobs(
        self, workspace_id: str, status: str | None = None, limit: int = _LIST_LIMIT_DEFAULT
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), _LIST_LIMIT_MAX))
        jobs = self._queries.list_jobs(workspace_id, status=status)
        return {
            "jobs": [_trim_summary(job) for job in jobs[:limit]],
            "returned": min(len(jobs), limit),
            "limit": limit,
        }

    def get_job_detail(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        self._job_in_workspace_or_404(workspace_id, job_id)
        return self._detail_payload(job_id)

    def _resolve_run(self, job_id: str, node_key: str | None, run_id: int | None) -> dict[str, Any]:
        if run_id is not None:
            run = self._job_db.get_node_run(job_id, run_id)
            if run is None:
                raise NotFoundError("Run not found")
            return run
        runs = self._job_db.list_node_runs(job_id)
        candidates = runs
        if node_key is not None:
            candidates = [run for run in runs if run.get("node_key") == node_key]
            if not candidates:
                raise NotFoundError("No runs recorded for node")
        else:
            # Diagnosis default: the latest failed run (the usual reason the
            # agent is called); fall back to the latest run overall.
            failed = [run for run in runs if run.get("status") == "failed"]
            candidates = failed or runs
        if not candidates:
            raise NotFoundError("No runs recorded for job")
        return max(candidates, key=lambda run: int(run["id"]))

    def get_node_logs(
        self,
        workspace_id: str,
        job_id: str,
        node_key: str | None = None,
        run_id: int | None = None,
    ) -> dict[str, Any]:
        self._job_in_workspace_or_404(workspace_id, job_id)
        run = self._resolve_run(job_id, node_key, run_id)
        payload = self._logs.read(job_id, int(run["id"]))
        log_text = str(payload["log"])
        truncated = bool(payload["truncated"])
        if len(log_text) > _LOG_TAIL_CHARS:
            log_text = log_text[-_LOG_TAIL_CHARS:]
            truncated = True
        return {
            "job_id": job_id,
            "run_id": int(run["id"]),
            "node_key": str(run.get("node_key") or ""),
            "status": str(run.get("status") or ""),
            "error_message": str(run.get("error_message") or ""),
            "log": log_text,
            "truncated": truncated,
        }

    def read_artifact(self, workspace_id: str, job_id: str, artifact_name: str) -> dict[str, Any]:
        self._job_in_workspace_or_404(workspace_id, job_id)
        payload = self._artifacts.read(job_id, artifact_name)
        content = str(payload["content"])
        truncated = False
        if len(content) > _ARTIFACT_MAX_CHARS:
            content = content[:_ARTIFACT_MAX_CHARS]
            truncated = True
        return {"name": str(payload["name"]), "content": content, "truncated": truncated}

    def compare_jobs(self, workspace_id: str, job_id_a: str, job_id_b: str) -> dict[str, Any]:
        self._job_in_workspace_or_404(workspace_id, job_id_a)
        self._job_in_workspace_or_404(workspace_id, job_id_b)
        detail_a = self._detail_payload(job_id_a)
        detail_b = self._detail_payload(job_id_b)
        nodes_a = {node["node_key"]: node for node in detail_a["nodes"]}
        nodes_b = {node["node_key"]: node for node in detail_b["nodes"]}
        # A's order first (the job under diagnosis), then B-only nodes.
        ordered_keys = [node["node_key"] for node in detail_a["nodes"]]
        ordered_keys += [key for key in nodes_b if key not in nodes_a]
        compared: list[dict[str, Any]] = []
        for key in ordered_keys:
            node_a = nodes_a.get(key)
            node_b = nodes_b.get(key)
            status_a = str(node_a["status"]) if node_a else "absent"
            status_b = str(node_b["status"]) if node_b else "absent"
            error_a = str(node_a["error_message"]) if node_a else ""
            error_b = str(node_b["error_message"]) if node_b else ""
            compared.append(
                {
                    "node_key": key,
                    "status_a": status_a,
                    "status_b": status_b,
                    "error_a": error_a,
                    "error_b": error_b,
                    "changed": status_a != status_b or error_a != error_b,
                }
            )
        newly_failed = sorted(
            row["node_key"]
            for row in compared
            if row["status_a"] == "failed" and row["status_b"] != "failed"
        )
        recovered = sorted(
            row["node_key"]
            for row in compared
            if row["status_a"] != "failed" and row["status_b"] == "failed"
        )
        return {
            "job_a": _trim_summary(detail_a["job"]),
            "job_b": _trim_summary(detail_b["job"]),
            "nodes": compared,
            "summary": {
                "nodes_changed": sum(1 for row in compared if row["changed"]),
                "newly_failed": newly_failed,
                "recovered": recovered,
            },
        }

    def get_job_context(
        self,
        session_id: str,
        user: dict[str, Any],
        job_id: str,
        node_key: str | None = None,
    ) -> dict[str, Any]:
        """Session-bound job context for the ``get_job_context`` MCP tool.

        Mirrors build_session_context's authorization: a bound run token may
        only read sessions of its own workspace; an unbound self-service token
        must belong to the session's workspace (admins pass). Mismatches are
        404 (not 403) so other workspaces' sessions/jobs cannot be probed.
        """
        session = self._job_db.get_studio_chat_session(session_id)
        if session is None or not self._may_read_session(session, user):
            raise NotFoundError("Chat session not found")
        workspace_id = str(session["workspace_id"])
        self._job_in_workspace_or_404(workspace_id, job_id)
        detail = self._detail_payload(job_id)
        focus = node_key or detail["job"].get("active_node_key")
        focus = str(focus) if focus else None
        return {
            "session_id": session_id,
            "workspace_id": workspace_id,
            "focus_node_key": focus,
            "job": detail,
            "recent_failures": self._recent_failures(workspace_id, focus, exclude_job=job_id),
        }

    def _recent_failures(
        self, workspace_id: str, focus_node_key: str | None, *, exclude_job: str
    ) -> list[dict[str, Any]]:
        """Other jobs' latest failed runs on the focus node — the
        flaky-or-new signal for the diagnosis."""
        if not focus_node_key:
            return []
        rows = self._failed_runs.list_failed_node_runs(workspace_id)
        failures = [
            {
                "job_id": str(row["job_id"]),
                "node_key": str(row["node_key"]),
                "failure_category": str(row.get("failure_category") or ""),
                "error_message": str(row.get("error_message") or ""),
                "finished_at": row.get("finished_at"),
            }
            for row in rows
            if row.get("node_key") == focus_node_key and str(row["job_id"]) != exclude_job
        ]
        return failures[:_RECENT_FAILURES_LIMIT]

    def _may_read_session(self, session: dict[str, Any], user: dict[str, Any]) -> bool:
        # Mirrors studio_chat_context._may_read_session (kept separate: that
        # helper is private to its module).
        bound = user.get("scoped_workspace_id")
        if bound is not None:
            return bool(session["workspace_id"] == bound)
        if user.get("role") == "admin":
            return True
        return (
            self._job_db.get_workspace_role(str(session["workspace_id"]), str(user["id"]))
            is not None
        )
