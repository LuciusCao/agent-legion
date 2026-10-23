from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, NoReturn, TypedDict

from server.app.events import JobEventManager
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.jobs.atomic_mutations import JobMutationConflict
from server.app.services.artifact_store import ArtifactStore
from server.app.services.job_artifact_gc import gc_deleted_job_artifacts, read_artifact_candidates
from server.app.services.job_deletion_trash import purge_deleted_job_files
from server.app.services.job_operation_error import JobOperationError
from server.app.services.job_rerun.batch_ops import batch_delete as _batch_delete
from server.app.settings import Settings
from server.app.storage_paths import ManagedPathError, resolve_job_dir

logger = logging.getLogger(__name__)


class JobDeleteResult(TypedDict):
    job_id: str
    operation: str
    status: str
    reason_code: str | None
    message: str | None


def _fail(job_id: str, reason_code: str | None, message: str) -> NoReturn:
    raise JobOperationError(job_id, "delete", "failed", None, reason_code, message)


class JobDeletionService:
    def __init__(
        self,
        job_db: JobQueries,
        lease_repo: ExecutorLeaseRepository,
        settings: Settings,
        clock: Callable[[], float] | None = None,
        job_event_manager: JobEventManager | None = None,
        job_event_buffer: Any | None = None,
        artifact_store: ArtifactStore | None = None,
        object_store: Any = None,
    ) -> None:
        self.job_db = job_db
        self.lease_repo = lease_repo
        self.settings = settings
        self.clock = clock
        self.job_event_manager = job_event_manager
        self.job_event_buffer = job_event_buffer
        self.artifact_store = artifact_store
        self.object_store = object_store

    def _now(self) -> datetime:
        if self.clock is not None:
            return datetime.fromtimestamp(self.clock(), tz=UTC)
        return datetime.now(UTC)

    def _result(
        self,
        job_id: str,
        status: str,
        reason_code: str | None = None,
        message: str | None = None,
    ) -> JobDeleteResult:
        return {
            "job_id": job_id,
            "operation": "delete",
            "status": status,
            "reason_code": reason_code,
            "message": message,
        }

    def delete(self, workspace_id: str, job_id: str) -> JobDeleteResult:
        job = self.job_db.get_job(job_id)
        if job is None:
            _fail(job_id, "not_found", "Job not found")
        if job["workspace_id"] != workspace_id:
            _fail(job_id, "not_found", "Job not found")
        if self.lease_repo.has_active_for_job(job_id, self._now()):
            _fail(job_id, "active_lease", "Cannot delete a job with an active executor lease")

        try:
            # 事务前 fail-closed：路径逃逸就拒绝删除，行与文件都不动。
            resolve_job_dir(job, self.settings.jobs_dir)
        except ManagedPathError as exc:
            _fail(job_id, "delete_failed", str(exc))

        artifact_candidates = read_artifact_candidates(self.artifact_store, job_id)
        # D12: snapshot the object-storage manifest rows before the job row
        # cascades them away; the objects are reclaimed after commit.
        object_rows = (
            self.object_store.rows_for_job(job_id)
            if self.object_store is not None and self.object_store.enabled
            else []
        )
        operation_id = f"{self._now().strftime('%Y%m%d%H%M%S%f')}-{uuid.uuid4().hex[:8]}"

        # #958：事务只做 DB 删除，不碰文件系统（文件 I/O 不再拉长 job-mutation
        # 锁的持有时间）；本地 job_dir / 日志在提交后由 purge_deleted_job_files
        # 清理，失败点终态与「先提交后移动」的取舍见 job_deletion_trash 模块。
        try:
            with self.job_db.lease_guarded_mutation(
                job_id,
                self._now(),
                reject_running_nodes=True,
            ) as conn:
                # node_runs (node_key, log_path) 快照在删行事务内、同一 conn 上读：
                # 持 job-mutation 锁、行级联消失前的最终集合，覆盖租约检查后到
                # 事务前才 claim 并完成的分片；#1113 前的旧扁平名日志只按它
                # 精确推导（新命名在 job 独占目录，整目录清理）。
                run_logs = self.job_db.list_node_run_logs_in_transaction(conn, job_id)
                self.job_db.delete_job_in_transaction(conn, job_id)
        except JobMutationConflict as exc:
            _fail(job_id, exc.reason_code, str(exc))
        except Exception as exc:
            # #204 broad-except audit: the transaction now carries only DB work
            # (the node_runs log snapshot read plus delete_job_in_transaction,
            # whose ValueError carries the
            # business refusals — a foreign-key rejection from a still-
            # referenced job, or a concurrent delete that already removed the
            # row — and is deliberately NOT caught before this arm so it is
            # normalized here). The filesystem has not been touched yet, so
            # every failure leaves the row and the local files intact and is
            # normalized to JobOperationError; the conflict arm above already
            # peeled off the concurrency case. logger.exception keeps the
            # traceback of the unexpected kind.
            logger.exception("Unexpected error deleting job %s", job_id)
            _fail(job_id, "delete_failed", str(exc))

        if purge_deleted_job_files(self.job_db, job, self.settings, operation_id, run_logs):
            # 同源 job 已重建（同一 id），或锁下复核因 DB 错误未能确认未重建：
            # 按 id 的 refs / 对象清理与删除广播可能打到新 job 上，一律跳过；
            # 旧 job 的孤儿 blob 交给 orphan GC（artifact_orphan_gc），对象存储
            # 侧孤儿由 scripts/gc-s3-jobs.py 回收。
            return self._result(job_id, "succeeded")
        gc_deleted_job_artifacts(self.artifact_store, job_id, artifact_candidates)
        if object_rows and self.object_store is not None:
            self.object_store.delete_objects(object_rows)
        self._broadcast_deleted(workspace_id, job_id)
        return self._result(job_id, "succeeded")

    def _broadcast_deleted(self, workspace_id: str, job_id: str) -> None:
        """删除广播前的身份复核（#1357，#1248 删除广播面）。

        purge 的锁在 rename 提交后即释放，广播若按裸 id 直发，落在「锁释放 →
        广播」之间提交的同源重建（确定性 id）会让客户端把**新** job 当作已删除
        移除。与 refs gc 面（``delete_refs_for_job`` 的 not exists 条件）同族：
        广播在 ``job-mutation:<id>`` 锁下复核 jobs 行仍不存在才发出——创建路径
        持同一把锁到提交（#1083），所以重建要么已落行（复核命中 → 跳过广播），
        要么等本锁释放后才提交，其 created 事件序在 deleted 之后，客户端不会
        误删活 job。锁事务因 DB 错误未能给出复核结果时无法排除重建，保守跳过
        广播（代价只是客户端下次全量刷新前多显示一个已删 job）；复核与广播失败
        绝不把已成功的删除变成 API 错误。广播本身是内存事件（buffer / SSE），
        不进锁事务的 DB 写面，锁事务保持只读短事务。
        """
        if self.job_event_buffer is None and self.job_event_manager is None:
            return
        try:
            with self.job_db.job_mutation_lock(job_id) as recreated:
                if recreated:
                    logger.info(
                        "Skip deletion broadcast of job %s: recreated under the same id", job_id
                    )
                    return
                if self.job_event_buffer is not None:
                    self.job_event_buffer.record_job_deleted(workspace_id, job_id)
                elif self.job_event_manager is not None:
                    stats = self.job_db.count_jobs_by_status(workspace_id)
                    self.job_event_manager.broadcast_job_deleted(workspace_id, job_id, stats)
        except Exception:
            # #204 broad-except audit: the jobs row is already committed as
            # deleted, so this locked recheck is best-effort — its only
            # failure kinds are DB errors (connection loss, lock/commit
            # failure) or an event-sink error. Failing here must not turn the
            # succeeded deletion into an API error; skipping the broadcast
            # only leaks a stale list entry until the client's next full
            # refresh. logger.exception keeps the traceback.
            logger.exception("Deletion broadcast recheck failed for job %s", job_id)

    def batch_delete(
        self, workspace_id: str, job_ids: list[str] | None = None, **kwargs: Any
    ) -> list[JobDeleteResult]:
        """Delete the selected jobs; kwargs take job_filter/exclude_ids."""
        return _batch_delete(self, workspace_id, job_ids, **kwargs)
