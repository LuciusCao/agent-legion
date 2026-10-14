"""#1279 codex r10：object_etag 启动回填的独立后台线程（与 sweeper_enabled 解耦）。

codex r10 P1：binder 曾挂在 MaterialTtlSweeperThread 上——受支持的
sweeper_enabled=False 逃生舱形态下该线程根本不启动，存量无 hash 行被
fail-closed/门禁永久拦住（非暂时态）。binder 是一次性任务（done 即退），
与永续 sweeper 生命周期本就不该绑定：本线程启动即跑、未清完（存储故障 /
静止期 pending）按间隔重试、done 后退出。

多副本：并发 binder 由绑定侧的 CAS 双钉兜住（安全），不加单实例租约——
一次性收敛任务，代价是有界重复 HEAD（幂等）。无对象存储 = 无材料特性，
``start_material_etag_backfill`` 返回 None 不建线程。
"""

from __future__ import annotations

import logging
import threading

from server.app.db.dialect import ConnectSource
from server.app.services.material_etag_backfill import backfill_legacy_material_etags
from server.app.services.material_ttl import DEFAULT_SWEEP_INTERVAL_SECONDS
from server.app.storage import ObjectStorage

logger = logging.getLogger(__name__)


class MaterialEtagBackfillThread:
    """一次性 etag 回填任务线程（启动即跑、间隔重试、done 即退）。"""

    def __init__(
        self,
        connect_source: ConnectSource,
        storage: ObjectStorage,
        *,
        interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS,
    ) -> None:
        # connect_source: JobQueries facade or bare DSN (BOUNDARY-DATA-001, #187).
        self._dsn = connect_source
        self._storage = storage
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        # binder 的静止期计时表跨轮存活（语义见 material_etag_backfill）。
        self._pending: dict[str, tuple[float, str, int, str]] = {}
        self.done = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="material-etag-backfill", daemon=True
        )
        self._thread.start()

    def run_once(self) -> None:
        stats = backfill_legacy_material_etags(self._dsn, self._storage, pending=self._pending)
        self.done = bool(stats["done"])
        if stats["bound"] or stats["marked_failed"] or stats["failed"] or stats["cas_lost"]:
            logger.info("material etag backfill pass: %s", stats)

    def _loop(self) -> None:
        while not self.done:
            try:
                self.run_once()
            except Exception:
                # #204 broad-except audit: 后台任务线程的生命维持（与
                # sweeper 线程同纪律）——DB 瞬断/存储故障/编程错误都记日志
                # 并按间隔重试，线程不死；done 后本线程自然退出。
                logger.exception("material etag backfill failed; retrying next interval")
            if self._stop_event.wait(self._interval_seconds):
                return

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            self._thread = None


def start_material_etag_backfill(
    connect_source: ConnectSource,
    storage: ObjectStorage | None,
    *,
    interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS,
) -> MaterialEtagBackfillThread | None:
    """启动一次性回填线程；无存储 = 无材料特性 → None（不建线程）。"""
    if storage is None:
        return None
    thread = MaterialEtagBackfillThread(connect_source, storage, interval_seconds=interval_seconds)
    thread.start()
    return thread
