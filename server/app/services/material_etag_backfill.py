"""#1279 codex r8/r9：存量 ready 材料行的 object_etag 启动回填（binder）。

v98 迁移给存量行留下 ``object_etag=''``（升级前完成、旧 PUT URL 仍在 1h
有效期内的无 hash 行仍处同尺寸静默覆盖窗口——列默认 '' 是过渡态，不是
永久豁免）。本模块在后台把它收口，统一不变量：**无 hash 材料的字节任何
时候都不能在没有已验证身份锚的情况下被消费**；锚只有两种——
content_hash（sha256 fail-closed，既有）与**静止期绑定的 etag**（绑定
时刻必须不存在仍可改写当前 key 对象的活 URL）。

- **静止期守卫**（r9 P1-1）：行的最后一次 URL 签发时刻对存量行不可考
  （ presign/retry/repair 都会换 key，但旧行没有记录签发时间），binder
  因此自计时：首次见到的候选记入 ``pending``（墙钟 + storage_key +
  upload_generation + 当时 HEAD 到的 etag），仅当同一身份标记持续满一个
  presign TTL（``materials._PRESIGN_EXPIRY_SECONDS``）才绑定——每次签发
  必伴随 generation/key 变化（行被改写→标记重置），故「标记满 TTL 未变」
  ⟺ 当前 key 的最后一个 URL 在绑定前已过期。绑定前 HEAD 的 etag 与标记
  不一致（活 URL 在窗口内改写了对象）同样重置计时。重启丢标记 =
  重新计时（安全方向）。
- **缺对象即失败**（r9 P1-3）：HEAD 不到对象的行直接翻 failed（与
  complete 缺对象 → failed 同语义；failed 行 repair 可救）——候选集
  自净，永久失败行不再挡住后续行的扫描（候选查询是 keyset 游标分页，
  不回头）。
- **CAS 绑定**：(object_etag='', storage_key, status='ready') 三钉——
  期间被 complete/repair/rebind 改动则让位（``cas_lost``），等下一轮。

语义边界（写死）：升级前已被毒化的行，静止期守卫也无能为力——它绑定的
可能是毒化 etag（既成事实无机制可分辨，只能靠 repair 重传）；静止期
守卫关掉的是升级前后「活 URL 窗口」内的新替换。

调用面：``MaterialEtagBackfillThread``（services/material_etag_binder.py，
独立一次性后台线程——codex r10 起不再挂 sweeper 线程，不受
sweeper_enabled 门控；启动即跑一次、未清完按间隔重试、done 即退），
pending 计时表随线程持有，重启安全重置。
DB 访问走 material_etag_identity.py 的 queries 层函数（BOUNDARY-DATA-001）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from botocore.exceptions import BotoCoreError, ClientError

from server.app.db.dialect import ConnectSource
from server.app.jobs.queries.material_etag_identity import (
    bind_material_etag_if_current,
    legacy_etag_backfill_candidates,
    mark_material_failed_if_unbound,
)
from server.app.services.materials import _PRESIGN_EXPIRY_SECONDS
from server.app.storage import ObjectStorage

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 200
DEFAULT_PAUSE_SECONDS = 0.05

# material_id → (首次见到/最近身份变化的墙钟, storage_key, upload_generation, etag)
PendingMarks = dict[str, tuple[float, str, int, str]]


def backfill_legacy_material_etags(
    connect_source: ConnectSource,
    storage: ObjectStorage,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    pause_seconds: float = DEFAULT_PAUSE_SECONDS,
    pending: PendingMarks | None = None,
    now: Callable[[], float] = time.monotonic,
) -> dict[str, int | bool]:
    """全量游标扫一轮候选；返回计数 + ``done`` 标记。

    ``done`` = 扫描到达候选集末尾且没有等待静止期满的 pending 行——调用方
    （binder 线程）据此停止回填；异常/中断则 done=False 下轮重扫。
    ``pending`` 计时表由调用方持有（跨轮存活；重启丢失 = 重新计时，安全
    方向）。缺对象翻 failed 的行即时退出候选集（自净）；静止期未满的行
    是暂时态，不计失败。
    """
    marks: PendingMarks = pending if pending is not None else {}
    stats: dict[str, int | bool] = {
        "bound": 0,
        "marked_failed": 0,
        "cas_lost": 0,
        "failed": 0,
        "pending": 0,
        "done": False,
    }
    cursor = None
    while True:
        batch = legacy_etag_backfill_candidates(connect_source, batch_size, after=cursor)
        if not batch:
            break
        for row in batch:
            material_id = str(row["id"])
            cursor = (row["created_at"], row["id"])
            identity = (str(row["storage_key"]), int(row["upload_generation"]))
            try:
                head = storage.head_object(identity[0])
            except (ClientError, BotoCoreError, OSError):
                # 存储面瞬态故障：计数跳过（不翻 failed——失败语义不明时
                # 不动行）；本行下轮重试。
                stats["failed"] = int(stats["failed"]) + 1
                logger.warning(
                    "material etag backfill: HEAD failed for %s", material_id, exc_info=True
                )
                continue
            if head is None:
                # 缺对象 = 永久失败（与 complete 缺对象同语义；repair 可救）；
                # 翻 failed 后退出候选集，不再挡扫描。
                if mark_material_failed_if_unbound(
                    connect_source, material_id, storage_key=identity[0]
                ):
                    stats["marked_failed"] = int(stats["marked_failed"]) + 1
                    marks.pop(material_id, None)
                continue
            if not head.etag:
                # 存储不给 etag：无法建立身份，跳过不翻行（计数供运维排查）。
                stats["failed"] = int(stats["failed"]) + 1
                continue
            mark = marks.get(material_id)
            if mark is None or mark[1:] != (identity[0], identity[1], head.etag):
                # 新候选或身份被改写（签发过新 URL/对象被换）：重新计时。
                marks[material_id] = (now(), identity[0], identity[1], head.etag)
                stats["pending"] = int(stats["pending"]) + 1
                continue
            if now() - mark[0] < _PRESIGN_EXPIRY_SECONDS:
                # 静止期未满：暂时态，等下一轮。
                stats["pending"] = int(stats["pending"]) + 1
                continue
            if bind_material_etag_if_current(
                connect_source,
                material_id,
                expected_etag="",
                new_etag=head.etag,
                storage_key=identity[0],
            ):
                stats["bound"] = int(stats["bound"]) + 1
                marks.pop(material_id, None)
            else:
                stats["cas_lost"] = int(stats["cas_lost"]) + 1
                marks.pop(material_id, None)
            time.sleep(pause_seconds)
    # done = 本轮扫描到的候选全部有着落（绑定/翻 failed/静止期外无
    # pending）且无失败——按本轮 pending 计数判，不按 marks 全局：行离开
    # 候选集（被 repair 走等）留下的陈旧标记不楔形 done（评审 P2-②；
    # 陈旧标记由身份比对兜住——key/代次变了只会重置计时，永不误绑）。
    stats["done"] = not stats["pending"] and stats["failed"] == 0
    return stats
