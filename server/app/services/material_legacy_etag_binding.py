"""#1316：存量无 hash 材料的 ETag 身份绑定（启动一次性任务 + CLI 共用）。

v98 之前完成的无 hash ready 行 ``object_etag=''``，没有内容身份：消费两侧只
能 size-only，升级前签出、升级时仍有效的 PUT URL（≤1h）的持有者同尺寸覆盖
不可检出。本模块在新 Host 启动时按**当前 HEAD** 为它们一次性绑定 ETag：

- 绑定之后，与 complete 绑定的新行同一语义：Host 物化与 v6 Worker 带
  If-Match，活 URL 的覆盖变成 412 响亮失败（repair 愈合）；绑定 CAS 同时
  代次 +1，未绑期间写入的 ``{id}-v2g{gen}`` 缓存不再命中（命中不重验）；
- 绑定之前（启动后到本线程扫到该行的秒级窗口）以及升级前发生的覆盖会被
  绑成身份——与 base 等价，无锚可分辨（设计 §6.4.1 已声明残留）；
- 旧 Worker（< v6）忽略 etag：绑定后的存量行对它**永久门禁**——旧 Worker
  可能持有升级前裸 ``material_id`` 地址的缓存且命中时不再 GET，任何排空期
  都无法证明其字节匹配已绑 ETag（codex r2 P1；谓词与权衡见
  jobs/queries/material_claim_gate 的模块 docstring）。

失败只报告、不改行（#1316 P4）：缺对象 / size 与行不符 / 存储不回 ETag /
HEAD 抛错 / CAS 让位，行保持 etag=''（= base 的 size-only 语义，不挂起、
不翻 failed）。驱动方：

- ``start_legacy_material_etag_binder``：Host lifespan 的生产形态
  （``start_worker``）在对象存储已配置时起一个守护线程；**不**受
  sweeper_enabled、code_capacity、claim 开关、workspace 暂停门控。每次启动
  都重跑（候选来自 DB，重启不丢进度），无候选即退；本轮有 HEAD/DB 异常时
  按间隔重扫，至多 ``_MAX_PASSES`` 轮后退出（剩余行仍是 base 语义，日志
  指向 CLI）。
- ``scripts/bind_legacy_material_etags.py``：运维手动重跑（幂等）。

多副本/与 CLI 并发：CAS 幂等，安全。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from server.app.db.dialect import ConnectSource
from server.app.jobs.queries.material_legacy_etag import (
    bind_legacy_material_etag,
    count_legacy_etag_bind_candidates,
    legacy_etag_bind_candidates,
)
from server.app.storage import ObjectStorage

logger = logging.getLogger(__name__)

_BATCH_SIZE = 200
_MAX_PASSES = 3
_RETRY_INTERVAL_SECONDS = 600.0


@dataclass
class LegacyEtagBindReport:
    """一轮绑定的结果；除 ``bound`` 外全部只报告、不改行。"""

    bound: list[str] = field(default_factory=list)
    missing_object: list[str] = field(default_factory=list)
    size_mismatch: list[str] = field(default_factory=list)
    empty_etag: list[str] = field(default_factory=list)
    head_errors: list[str] = field(default_factory=list)
    cas_lost: list[str] = field(default_factory=list)

    @property
    def unresolved(self) -> int:
        return (
            len(self.missing_object)
            + len(self.size_mismatch)
            + len(self.empty_etag)
            + len(self.head_errors)
            + len(self.cas_lost)
        )

    def summary(self) -> dict[str, int]:
        return {
            "bound": len(self.bound),
            "missing_object": len(self.missing_object),
            "size_mismatch": len(self.size_mismatch),
            "empty_etag": len(self.empty_etag),
            "head_errors": len(self.head_errors),
            "cas_lost": len(self.cas_lost),
        }


def bind_legacy_material_etags(
    connect_source: ConnectSource,
    storage: ObjectStorage,
    *,
    apply: bool = True,
    batch_size: int = _BATCH_SIZE,
) -> LegacyEtagBindReport:
    """扫一遍全部候选并逐行绑定（``apply=False`` 只报告将绑定的行）。"""
    report = LegacyEtagBindReport()
    after: tuple[Any, Any] | None = None
    while True:
        rows = legacy_etag_bind_candidates(connect_source, batch_size, after=after)
        if not rows:
            return report
        after = (rows[-1]["created_at"], rows[-1]["id"])
        for row in rows:
            _bind_one(connect_source, storage, row, report, apply=apply)


def _bind_one(
    connect_source: ConnectSource,
    storage: ObjectStorage,
    row: dict[str, Any],
    report: LegacyEtagBindReport,
    *,
    apply: bool,
) -> None:
    material_id = str(row["id"])
    try:
        head = storage.head_object(str(row["storage_key"]))
    except Exception:
        # #204 broad-except audit: 存储面的任意故障（网络 / 凭据 / 5xx）对单行
        # 只报告、不改行——该行保持 etag=''（base 语义），由下一轮 / CLI 重试。
        logger.warning("legacy etag bind: HEAD failed for material %s", material_id, exc_info=True)
        report.head_errors.append(material_id)
        return
    if head is None:
        report.missing_object.append(material_id)
        return
    if int(head.size_bytes) != int(row["size_bytes"]):
        report.size_mismatch.append(material_id)
        return
    if not head.etag:
        report.empty_etag.append(material_id)
        return
    if not apply:
        report.bound.append(material_id)
        return
    if bind_legacy_material_etag(
        connect_source,
        material_id,
        object_etag=head.etag,
        storage_key=str(row["storage_key"]),
        upload_generation=int(row["upload_generation"]),
    ):
        report.bound.append(material_id)
    else:
        report.cas_lost.append(material_id)


def _log_report(report: LegacyEtagBindReport) -> None:
    if report.bound:
        logger.info("legacy material etag binding: %s", report.summary())
    if report.missing_object or report.size_mismatch or report.empty_etag:
        # 永久性问题：行保持 etag=''（size-only），需要运维看一眼（repair 重传）。
        logger.warning(
            "legacy material etag binding left rows unbound (kept size-only): "
            "missing_object=%s size_mismatch=%s empty_etag=%s",
            report.missing_object[:20],
            report.size_mismatch[:20],
            report.empty_etag[:20],
        )


class LegacyMaterialEtagBinderThread:
    """一次性绑定任务线程：启动即扫、异常按间隔重扫（有界）、完成即退。"""

    def __init__(
        self,
        connect_source: ConnectSource,
        storage: ObjectStorage,
        *,
        retry_interval_seconds: float = _RETRY_INTERVAL_SECONDS,
        max_passes: int = _MAX_PASSES,
    ) -> None:
        # connect_source: JobQueries facade or bare DSN (BOUNDARY-DATA-001, #187).
        self._dsn = connect_source
        self._storage = storage
        self._retry_interval_seconds = retry_interval_seconds
        self._max_passes = max_passes
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.passes = 0
        self.done = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self.run, name="legacy-material-etag-binder", daemon=True
        )
        self._thread.start()

    def run_pass(self) -> bool:
        """跑一轮；返回本轮是否干净（无需重扫）。"""
        self.passes += 1
        try:
            if count_legacy_etag_bind_candidates(self._dsn) == 0:
                return True
            report = bind_legacy_material_etags(self._dsn, self._storage)
        except Exception:
            # #204 broad-except audit: 后台一次性任务的生命维持——DB 瞬断等
            # 记日志后按间隔重扫；任何情况下未绑定的行都保持 base 语义。
            logger.exception("legacy material etag binding pass failed")
            return False
        _log_report(report)
        # 只有暂时性失败（HEAD 抛错 / CAS 让位）值得重扫；缺对象等永久性
        # 问题重扫也不会变，只报告。
        return not (report.head_errors or report.cas_lost)

    def run(self) -> None:
        while self.passes < self._max_passes:
            if self.run_pass():
                self.done = True
                return
            if self._stop_event.wait(self._retry_interval_seconds):
                return
        logger.warning(
            "legacy material etag binding gave up after %s passes; unbound rows stay "
            "size-only — rerun scripts/bind_legacy_material_etags.py --apply",
            self.passes,
        )

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            self._thread = None


def start_legacy_material_etag_binder(
    connect_source: ConnectSource, storage: ObjectStorage | None
) -> LegacyMaterialEtagBinderThread | None:
    """起一次性绑定线程；无对象存储 = 无材料特性 → None（不建线程）。"""
    if storage is None:
        return None
    thread = LegacyMaterialEtagBinderThread(connect_source, storage)
    thread.start()
    return thread
