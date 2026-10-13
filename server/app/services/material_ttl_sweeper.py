"""Slow-cadence driver thread for the materials TTL sweep.

Split from :mod:`material_ttl` (budget): the sweep functions are the
unit-testable core; this thread mirrors JobArtifactMaintenanceThread's loop
discipline around them.

Also carries the #1279 codex r8 startup backfill of ``materials.object_etag``
（存量 ready 行的 ETag 身份建立，语义见 material_etag_backfill.py）：
线程启动即跑一次（不等首个 interval——升级窗口要尽早关闭），未清空
（存储故障等零进展）则每个 sweep 间隔重试；sweeper 副本单实例运行，
无多副本并发问题。
"""

from __future__ import annotations

import logging
import threading

from server.app.db.dialect import ConnectSource
from server.app.services.material_etag_backfill import backfill_legacy_material_etags
from server.app.services.material_ttl import (
    DEFAULT_SWEEP_INTERVAL_SECONDS,
    collect_expired_materials,
    expire_due_materials,
)
from server.app.storage import ObjectStorage

logger = logging.getLogger(__name__)


class MaterialTtlSweeperThread:
    """Slow-cadence driver; mirrors JobArtifactMaintenanceThread's discipline.

    The first run happens after one full interval: the sweep is low-urgency
    and a boot-time scan only competes with startup work.（例外：etag 回填
    在启动时立即跑，见模块 docstring。）
    """

    def __init__(
        self,
        database_dsn: ConnectSource,
        storage: ObjectStorage | None,
        *,
        interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS,
    ) -> None:
        # database_dsn: JobQueries facade or bare DSN (BOUNDARY-DATA-001, #187).
        self._dsn = database_dsn
        self._storage = storage
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._backfill_done = False
        # binder 的静止期计时表（#1279 r9）：跨轮存活；重启丢失 = 重新计时
        # （安全方向）。语义见 material_etag_backfill 模块 docstring。
        self._backfill_pending: dict[str, tuple[float, str, int, str]] = {}

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="material-ttl-sweeper", daemon=True)
        self._thread.start()

    def _run_backfill(self) -> None:
        """#1279 codex r8：存量 ready 行 object_etag 回填（一次性收口）。

        无存储 = 无材料特性（run_once 同前提），标记完成不再重试。
        """
        if self._storage is None:
            self._backfill_done = True
            return
        stats = backfill_legacy_material_etags(
            self._dsn, self._storage, pending=self._backfill_pending
        )
        self._backfill_done = bool(stats["done"])
        if stats["bound"] or stats["missing"] or stats["failed"] or stats["cas_lost"]:
            logger.info("material etag backfill pass: %s", stats)

    def run_once(self) -> None:
        # No object storage means no materials feature: rows can only reach
        # ready/expired through the storage-backed upload flow.
        if self._storage is None:
            return
        expired = expire_due_materials(self._dsn)
        if expired:
            logger.info("materials TTL sweep expired %d material(s)", expired)
        deleted = collect_expired_materials(self._dsn, self._storage)
        if deleted:
            logger.info("materials TTL sweep collected %d material(s)", deleted)

    def _loop(self) -> None:
        try:
            # #1279：etag 回填启动即跑（升级窗口尽早关闭）；纳入生命维持——
            # DB 瞬断不得带走整个 sweeper 线程（连带 TTL sweep，评审 P2）。
            # #204 broad-except audit: 与下方 sweep 循环同一生命维持纪律
            # （JobArtifactMaintenanceThread 先例）：启动期回填失败的全部
            # 形态（DB 不可达/存储故障/编程错误）都记日志并等下个间隔重试，
            # 线程不死。
            self._run_backfill()
        except Exception:
            logger.exception("material etag backfill at startup failed; retrying each interval")
        while not self._stop_event.wait(self._interval_seconds):
            try:
                if not self._backfill_done:
                    self._run_backfill()
                self.run_once()
            except Exception:
                # #204 broad-except audit: the sweeper thread's life support
                # (same discipline as JobArtifactMaintenanceThread, #251).
                # run_once walks the materials table and the object store —
                # a DB restart or storage outage mid-pass must not kill the
                # only thread performing expiry; the traceback is logged and
                # the next interval is the retry. Narrow per-material
                # failures are already contained inside
                # collect_expired_materials' own per-row catches.
                logger.exception("materials TTL sweep failed")

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            self._thread = None
