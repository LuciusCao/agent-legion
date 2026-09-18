import logging
from pathlib import Path
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from server.app.jobs import JobQueries
from server.app.services import job_artifact_names
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.job_artifact_raw import RawArtifact, open_raw_artifact
from server.app.services.job_artifact_row_prefix import refuse_row_outside_job_prefix
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
        # #631 攻击复审 M1：控制字符 job_id（%00）会在 SQL 参数化时炸
        # psycopg DataError（500）；形状早拒（400）后再查库。裸路由
        # （/jobs/{job_id}/artifacts/...）与外部端点共用这道门。
        if not job_artifact_names.is_plausible_job_id(job_id):
            raise InvalidOperationError("Invalid job id")
        job = self.job_db.get_job(job_id)
        if job is None:
            raise NotFoundError("Job not found")
        return job

    def _artifact_path(self, job: dict[str, Any], artifact_name: str) -> Path:
        # #631 review P2-1: 声明产物可以是 job_dir 相对子路径（reports/
        # final.json——Worker 解包/promote 都保留子目录）。安全边界是
        # 包含性校验而非「不含 /」：绝对名、``..`` 段与反斜杠照旧拒绝
        # （与 result_unpack / download_remote_artifact 同一规则）。
        # #631 攻击复审 M1/M2：名字先过共享白名单（NUL/控制字符/超长段
        # → 400 而非文件系统调用炸 500；runs/ 与点前缀段与清单剪枝同一
        # 规则，消除「清单不列但可达」的不对称），再做包含性校验。
        if not job_artifact_names.is_downloadable_artifact_name(artifact_name):
            raise InvalidOperationError("Invalid artifact name")

        base = resolve_job_dir(job, self.job_db.jobs_dir)
        path = (base / artifact_name).resolve()
        if not path.is_relative_to(base):
            raise InvalidOperationError("Invalid artifact path")
        return path

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
        if (stored := self._read_object(job, artifact_name)) is not None:
            return stored
        raise NotFoundError("Artifact not found")

    def _read_object(self, job: dict[str, Any], name: str) -> dict[str, Any] | None:
        """对象副本的文本读（read() 的回退分支）：行先过 H1 前缀兜底，
        再透明解压读内容字节（#338），存储故障按 None 降级。"""
        store = self.object_store
        if store is None or not store.enabled:
            return None
        job_id = str(job["id"])
        row = store.lookup(job_id, name)
        if row is None or refuse_row_outside_job_prefix(row, job):
            # #631 攻击复审 H1（文本分支）：storage_key 指向本 job 前缀之外
            # 的行按未找到处理——读路径不为写歪的行跨 workspace 取字节。
            return None
        try:
            stream = store.open_stream(row)
            content = stream.read().decode("utf-8")
        except (ClientError, BotoCoreError, OSError, UnicodeDecodeError):
            # 对象可能被 bucket lifecycle 删除（NoSuchKey）或存储暂时不可用：
            # 按未找到处理，让 read() 落到 404 而不是冒泡 500。UnicodeDecodeError
            # 是二进制产物走了文本端点：字节由 raw 端点负责，这里继续降级查找。
            # #204: 这四类就是这个 try 块的完整失败空间——boto3 数据面
            # （ClientError/BotoCoreError）、流式读的中断（OSError，含本地
            # fake 与远端连接重置）、解码失败；其余按编程错误冒泡。
            logger.warning("failed to read artifact %s of job %s", name, job_id, exc_info=True)
            return None
        return {"name": name, "content": content}

    def open_raw(
        self, job_id: str, artifact_name: str, range_header: str | None = None
    ) -> RawArtifact:
        """二进制产物句柄；range_header 只对对象存储分支生效（见 raw 模块）。"""
        job = self._job_or_404(job_id)
        path = self._artifact_path(job, artifact_name)
        return open_raw_artifact(
            path, self.object_store, job_id, artifact_name, range_header, job=job
        )

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
        path = self._artifact_path(job, artifact_name)
        return open_raw_artifact(
            path,
            self.object_store,
            job_id,
            artifact_name,
            range_header,
            manifest_first=True,
            job=job,
        )

    def reject_subpath(self, job_id: str) -> None:
        self._job_or_404(job_id)
        raise InvalidOperationError("Invalid job path")
