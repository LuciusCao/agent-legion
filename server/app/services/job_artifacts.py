import logging
from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.job_artifact_raw import RawArtifact, open_raw_artifact
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.storage_paths import resolve_job_dir

logger = logging.getLogger(__name__)


class JobArtifactService:
    def __init__(
        self,
        job_db: JobQueries,
        object_store: JobArtifactObjectStore | None = None,
    ) -> None:
        self.job_db = job_db
        # D12 read path: object storage first, local job_dir fallback (legacy
        # jobs were never uploaded; an unconfigured instance has no rows).
        self.object_store = object_store

    def _job_or_404(self, job_id: str) -> dict[str, Any]:
        job = self.job_db.get_job(job_id)
        if job is None:
            raise NotFoundError("Job not found")
        return job

    def _artifact_path(self, job: dict[str, Any], artifact_name: str) -> Path:
        if "/" in artifact_name or "\\" in artifact_name or artifact_name in {"", ".", ".."}:
            raise InvalidOperationError("Invalid artifact name")

        base = resolve_job_dir(job, self.job_db.jobs_dir)
        path = (base / artifact_name).resolve()
        if path.parent != base:
            raise InvalidOperationError("Invalid artifact path")
        return path

    def _read_object(self, job_id: str, artifact_name: str) -> dict[str, Any] | None:
        store = self.object_store
        if store is None or not store.enabled:
            return None
        row = store.lookup(job_id, artifact_name)
        if row is None:
            return None
        try:
            stream = store.open_stream(row)
            content = stream.read().decode("utf-8")
        except Exception:
            # 对象可能被 bucket lifecycle 删除（NoSuchKey）或存储暂时不可用：
            # 按未找到处理，让 read() 落到 404 而不是冒泡 500。
            logger.warning(
                "failed to read artifact %s of job %s from object storage",
                artifact_name,
                job_id,
                exc_info=True,
            )
            return None
        return {"name": artifact_name, "content": content}

    def read(self, job_id: str, artifact_name: str) -> dict[str, Any]:
        job = self._job_or_404(job_id)
        path = self._artifact_path(job, artifact_name)
        if path.exists() and path.is_file():
            try:
                return {"name": artifact_name, "content": path.read_text(encoding="utf-8")}
            except (OSError, UnicodeDecodeError):
                # TOCTOU：淘汰线程可能在 exists() 与 read_text() 之间 unlink，
                # 落到对象存储副本而不是冒泡 500。UnicodeDecodeError 是二进制
                # 产物走了文本端点：字节由 raw 端点负责，这里继续降级查找。
                pass
        stored = self._read_object(job_id, artifact_name)
        if stored is not None:
            return stored
        raise NotFoundError("Artifact not found")

    def open_raw(self, job_id: str, artifact_name: str) -> RawArtifact:
        """Locate a binary-servable artifact (see job_artifact_raw module)."""
        job = self._job_or_404(job_id)
        path = self._artifact_path(job, artifact_name)
        return open_raw_artifact(job, path, self.object_store, job_id, artifact_name)

    def reject_subpath(self, job_id: str) -> None:
        self._job_or_404(job_id)
        raise InvalidOperationError("Invalid job path")
