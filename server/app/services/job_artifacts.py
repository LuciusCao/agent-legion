import logging
from pathlib import Path, PurePosixPath
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from server.app.jobs import JobQueries
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.job_artifact_raw import RawArtifact, open_raw_artifact
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.storage_paths import resolve_job_dir

logger = logging.getLogger(__name__)


class JobArtifactService:
    def __init__(
        self, job_db: JobQueries, object_store: JobArtifactObjectStore | None = None
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
        # #631 review P2-1: 声明产物可以是 job_dir 相对子路径（reports/
        # final.json——Worker 解包/promote 都保留子目录）。安全边界是
        # 包含性校验而非「不含 /」：绝对名、``..`` 段与反斜杠照旧拒绝
        # （与 result_unpack / download_remote_artifact 同一规则）。
        relative = PurePosixPath(artifact_name)
        if (
            not relative.parts
            or relative.is_absolute()
            or ".." in relative.parts
            or "\\" in artifact_name
        ):
            raise InvalidOperationError("Invalid artifact name")

        base = resolve_job_dir(job, self.job_db.jobs_dir)
        path = (base / relative).resolve()
        if not path.is_relative_to(base):
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
            # #338: open_stream 对 .gz 对象透明解压，这里拿到的始终是未压缩
            # 内容字节（content_hash 的语义），两种存储形态同一读法。
            stream = store.open_stream(row)
            content = stream.read().decode("utf-8")
        except (ClientError, BotoCoreError, OSError, UnicodeDecodeError):
            # 对象可能被 bucket lifecycle 删除（NoSuchKey）或存储暂时不可用：
            # 按未找到处理，让 read() 落到 404 而不是冒泡 500。UnicodeDecodeError
            # 是二进制产物走了文本端点：字节由 raw 端点负责，这里继续降级查找。
            # #204: 这四类就是这个 try 块的完整失败空间——boto3 数据面
            # （ClientError/BotoCoreError）、流式读的中断（OSError，含本地
            # fake 与远端连接重置）、解码失败；其余按编程错误冒泡。
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

    def open_raw(
        self, job_id: str, artifact_name: str, range_header: str | None = None
    ) -> RawArtifact:
        """二进制产物句柄；range_header 只对对象存储分支生效（见 raw 模块）。"""
        job = self._job_or_404(job_id)
        path = self._artifact_path(job, artifact_name)
        return open_raw_artifact(path, self.object_store, job_id, artifact_name, range_header)

    def open_raw_current(
        self, job_id: str, artifact_name: str, range_header: str | None = None
    ) -> RawArtifact:
        """Manifest-first variant for surfaces that publish the manifest row
        (#631 external access): with a ``job_artifacts`` row the object IS the
        advertised copy (the listing answers the row's content_hash /
        uploaded_at; the local cache may hold stale bytes — EXEC-ARTIFACT-
        STORE-001), so it is served first; the local copy only without a row.
        """
        job = self._job_or_404(job_id)
        return open_raw_artifact(
            self._artifact_path(job, artifact_name),
            self.object_store,
            job_id,
            artifact_name,
            range_header,
            manifest_first=True,
        )

    def reject_subpath(self, job_id: str) -> None:
        self._job_or_404(job_id)
        raise InvalidOperationError("Invalid job path")
