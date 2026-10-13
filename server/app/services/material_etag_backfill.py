"""#1279 codex r8：存量 ready 材料行的 object_etag 启动回填。

v98 迁移给存量行留下 ``object_etag=''``（升级前完成、旧 PUT URL 仍在 1h
有效期内的无 hash 行，升级后仍可被同尺寸静默覆盖——列默认 '' 是过渡态，
不是永久豁免）。本模块在后台把它收口：逐行 HEAD 取当前 ETag，CAS
（expected='' 且 storage_key 未变）落库。幂等可重入：跑到一半重启就重扫；
对象缺失（行指向的对象已不存在）跳过并计数；与 complete/repair/rebind
并发由 CAS 兜住（对方先动则让位，等下一轮）。

语义边界（写死）：升级前已被毒化的行，回填到的是毒化 etag——既成事实无
机制可分辨（只能靠 repair 重传），回填关闭的是升级之后的新窗口。完成前
这些行维持 size-only 现状放行（瞬态窗口）。

调用面：``MaterialTtlSweeperThread``（sweeper 副本单实例，启动即跑一次、
未清空则按 sweep 间隔重试）。DB 访问走 material_repair.py 的 queries 层
函数（BOUNDARY-DATA-001）。
"""

from __future__ import annotations

import logging
import time

from botocore.exceptions import BotoCoreError, ClientError

from server.app.db.dialect import ConnectSource
from server.app.jobs.queries.material_etag_identity import (
    bind_material_etag_if_current,
    legacy_etag_backfill_candidates,
)
from server.app.storage import ObjectStorage

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 200
DEFAULT_PAUSE_SECONDS = 0.05


def backfill_legacy_material_etags(
    connect_source: ConnectSource,
    storage: ObjectStorage,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    pause_seconds: float = DEFAULT_PAUSE_SECONDS,
) -> dict[str, int | bool]:
    """回填一轮到清空（或零进展即止）；返回计数 + ``done`` 标记。

    ``done`` False 表示本轮零进展（存储故障等），调用方按自身节奏重试。
    缺对象/无 etag 的行永远留在候选集里——靠零进展闸退出本轮，而不是
    死循环。
    """
    stats: dict[str, int | bool] = {
        "bound": 0,
        "missing": 0,
        "cas_lost": 0,
        "failed": 0,
        "done": False,
    }
    while True:
        candidates = legacy_etag_backfill_candidates(connect_source, batch_size)
        if not candidates:
            stats["done"] = True
            return stats
        bound_this_batch = 0
        for row in candidates:
            material_id = str(row["id"])
            storage_key = str(row["storage_key"])
            try:
                head = storage.head_object(storage_key)
            except (ClientError, BotoCoreError, OSError):
                # 存储面瞬态故障：计数并让零进展闸收尾，下一个 sweep 间隔重试。
                stats["failed"] = int(stats["failed"]) + 1
                logger.warning(
                    "material etag backfill: HEAD failed for %s", material_id, exc_info=True
                )
                continue
            if head is None or not head.etag:
                # 对象缺失（或存储不给 etag）：无法建立身份，跳过并计数。
                stats["missing"] = int(stats["missing"]) + 1
                continue
            if bind_material_etag_if_current(
                connect_source,
                material_id,
                expected_etag="",
                new_etag=head.etag,
                storage_key=storage_key,
            ):
                stats["bound"] = int(stats["bound"]) + 1
                bound_this_batch += 1
            else:
                # 期间被 complete/repair/rebind 改动：让位，下一轮重扫。
                stats["cas_lost"] = int(stats["cas_lost"]) + 1
            if pause_seconds > 0:
                time.sleep(pause_seconds)
        if bound_this_batch == 0:
            return stats
