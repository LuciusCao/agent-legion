"""External artifact-access service (#631).

Reads one job's status and artifact manifest through a workspace-prefixed
scope: the workspace binding doubles as the existence check (a job id from
another workspace is a 404, not a 403, so ids cannot be probed across
workspaces). All data access stays behind the JobQueries facade
(BOUNDARY-DATA-001); the listing merges the object-storage manifest (the
authoritative copy, EXEC-ARTIFACT-STORE-001) with legacy local job_dir names.
"""

from __future__ import annotations

from typing import Any, cast

from server.app.jobs import JobQueries
from server.app.services.job_artifact_media import raw_media_type
from server.app.services.job_artifact_names import (
    is_downloadable_artifact_name,
    is_plausible_job_id,
)
from server.app.services.job_artifact_raw_types import RawArtifact
from server.app.services.job_artifact_row_prefix import refuse_row_outside_job_prefix
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.job_query_presenters import _declared_output_names, artifact_names_deep
from server.app.settings import Settings


def _listing_rows(store: Any, job: dict[str, Any]) -> tuple[set[str], dict[str, dict[str, Any]]]:
    """Manifest-row view for the listing surfaces, symmetric with the raw
    endpoint (#703 review MEDIUM-2).

    ``rows_for_job`` is uploaded_at-ascending; dict assignment keeps the
    NEWEST row per name — the same row ``lookup`` resolves for the download,
    so the listing and the raw endpoint judge one name by one row. A row is
    servable when its name passes the download whitelist (raw answers 400
    otherwise) and its storage_key stays inside the job's prefix (the H1
    read-side guard answers 404 otherwise) — anything else must not be
    advertised. Returns (all row names, servable rows): callers also remove
    row-having names from the local listing, because the manifest-first raw
    read short-circuits on the row (a refused row is a 404 even when a local
    copy exists), so the local copy is never the download truth for them.
    """
    latest = {str(row["name"]): row for row in store.rows_for_job(str(job["id"]))}
    return set(latest), {
        name: row
        for name, row in latest.items()
        if is_downloadable_artifact_name(name) and not refuse_row_outside_job_prefix(row, job)
    }


class ExternalArtifactAccessService:
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        object_store: Any | None = None,
        artifact_service: Any | None = None,
    ) -> None:
        self.job_db = job_db
        self.settings = settings
        # object_store：typed Any（组合根传 JobArtifactObjectStore，测试桩注入）；
        # artifact_service：raw 句柄查找（JobArtifactService，同裸路由实例）。
        self.object_store = object_store
        self._artifact_service = artifact_service

    def _job_in_workspace_or_404(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        # #631：workspace 检查兼存在性检查——跨 workspace 的 job_id 是 404
        # 不是 403（不可探测）。攻击复审 M1：控制字符 job_id（%00）在 SQL
        # 参数化时炸 psycopg DataError（500）；形状早拒 400。
        if not is_plausible_job_id(job_id):
            raise InvalidOperationError("Invalid job id")
        job = self.job_db.get_job(job_id)
        if job is None or str(job["workspace_id"]) != workspace_id:
            raise NotFoundError("Job not found")
        return job

    def require_job_in_workspace(self, workspace_id: str, job_id: str) -> None:
        """Guard-only variant（沿用 JobArtifactService 读路径的路由用）。"""
        self._job_in_workspace_or_404(workspace_id, job_id)

    def _object_storage_enabled(self) -> bool:
        return self.object_store is not None and bool(self.object_store.enabled)

    def _enabled_store(self) -> Any | None:
        """The store when object storage is configured (typed local so mypy
        sees the None-guard the way the runtime does)."""
        if self.object_store is None or not self.object_store.enabled:
            return None
        return self.object_store

    def _node_progress(self, job_id: str) -> tuple[int, int, str]:
        nodes = self.job_db.list_job_nodes(job_id)
        statuses = [(str(n.get("status")), str(n.get("error_message") or "")) for n in nodes]
        completed = sum(1 for s, _ in statuses if s == "completed")
        failed = next((msg[:240] for s, msg in statuses if s == "failed"), "")
        return completed, len(nodes), failed

    def status(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        """Lightweight status view: id/scope/status/outcome/timestamps plus
        the artifact name list (external callers poll this, not JobDetail)."""
        job = self._job_in_workspace_or_404(workspace_id, job_id)
        completed, total, error_summary = self._node_progress(job_id)
        return {
            "job_id": str(job["id"]),
            "workspace_id": str(job["workspace_id"]),
            "status": str(job["status"]),
            "outcome": str(job.get("outcome") or ""),
            "created_at": job.get("created_at"),
            "updated_at": job.get("updated_at"),
            "error_summary": error_summary,
            "completed_nodes": completed,
            "total_nodes": total,
            "artifacts": self._artifact_names(job),
        }

    def _artifact_names(self, job: dict[str, Any]) -> list[str]:
        # 递归扫描（#631 review P2-1）：local-only 子路径产物（reports/
        # final.json）也在名单里，名单成员与可下载名一一对应。list 与
        # status 两个列举面同门（同一份条目管线，见 _artifact_entries）。
        return sorted(str(entry["name"]) for entry in self._artifact_entries(job))

    def _artifact_entries(self, job: dict[str, Any]) -> list[dict[str, Any]]:
        # 对象 manifest 行 + 本地 job_dir 名的合并清单（list 与 status 共
        # 用）。enabled 门控：实例摘掉存储配置后清单里的名字读不到，不再
        # 列出。行过滤：名字过下载侧白名单（raw 必拒的名字不列，#631
        # codex3）；最新行的 storage_key 越界（H1 兜底 404）不列，有行名字
        # 一律以行为准、本地副本不回填（manifest-first 读到行就短路本地
        # 分支，被拒行是 404，#703 复审 M2）。本地扫描过声明门（#703
        # codex4 P2-1）：快照解析一次，walk 与下载门共用。
        store = self._enabled_store()
        row_names: set[str] = set()
        object_backed: dict[str, dict[str, Any]] = {}
        if store is not None:
            row_names, object_backed = _listing_rows(store, job)
        entries = [self._entry_from_row(row) for row in object_backed.values()]
        local_names = set(artifact_names_deep(job, self.settings, _declared_output_names(job)))
        if store is not None:
            local_names -= row_names
        entries.extend(self._local_entry(name) for name in sorted(local_names))
        return entries

    def open_raw_current(
        self, workspace_id: str, job_id: str, artifact_name: str, range_header: str | None = None
    ) -> RawArtifact:
        """外部 raw 读：workspace 守卫 + 本地子路径下载门，再走
        manifest-first 句柄（有行读对象副本，无行回落本地文件）。

        #703 codex round 4 (P2-1)：本地 job_dir 是执行暂存面——名字没进
        job 快照的声明 outputs（scratch/debug.json 这类未声明嵌套文件）
        不可下载（404）；对象 manifest 行不受此门（行是执行产物的登记
        面），有行照常读。名字白名单先于声明门（#631 M1/M2：畸形名/越界
        名保持 400 语义，不被声明门的 404 吞掉）。
        """
        job = self._job_in_workspace_or_404(workspace_id, job_id)
        if not is_downloadable_artifact_name(artifact_name):
            raise InvalidOperationError("Invalid artifact name")
        store = self._enabled_store()
        # 无 manifest 行的名字才过声明门（行是登记面，不受此门）。
        if (store is None or store.lookup(str(job["id"]), artifact_name) is None) and (
            artifact_name not in _declared_output_names(job)
        ):
            raise NotFoundError("Artifact not found")
        service = self._artifact_service
        if service is None:
            raise NotFoundError("Artifact not found")
        # 组合根传 JobArtifactService（同裸路由实例）；cast 只收窄 Any。
        return cast(RawArtifact, service.open_raw_current(job_id, artifact_name, range_header))

    def list_artifacts(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        """Manifest listing for the job's CURRENT artifacts (rerun semantics
        #508): one entry per name — the manifest row's content_hash/uploaded_at
        identify which execution produced the bytes being served. Jobs that
        are still running list what exists so far (stable: callers poll)."""
        job = self._job_in_workspace_or_404(workspace_id, job_id)
        return {
            "job_id": job_id,
            "workspace_id": workspace_id,
            "status": str(job["status"]),
            "artifacts": self._artifact_entries(job),
            "object_storage_enabled": self._object_storage_enabled(),
        }

    def _entry_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        name = str(row["name"])
        size = row.get("size_bytes")
        return {
            "name": name,
            "storage": "object",
            "node_key": str(row.get("node_key") or ""),
            "size_bytes": int(size) if isinstance(size, int) else None,
            "content_hash": str(row.get("content_hash") or ""),
            "uploaded_at": row.get("uploaded_at"),
            "media_type": raw_media_type(name),
        }

    def _local_entry(self, name: str) -> dict[str, Any]:
        # Legacy job_dir-only artifact (never uploaded): no manifest row, so
        # size/hash are unknown at listing time; the raw endpoint still
        # serves it from the local copy.
        return {
            "name": name,
            "storage": "local",
            "node_key": "",
            "size_bytes": None,
            "content_hash": "",
            "uploaded_at": None,
            "media_type": raw_media_type(name),
        }
