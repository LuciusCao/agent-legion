"""结果上报的 Host 应答分级与诚实判败降级（#959）。

Host 结果端点（``server/app/routes/agent_workers.py`` 的 ``result``）的应答
语义决定 Worker 侧处置：

- 204：已提交终态。
- 409：本 attempt 不再拥有租约（precheck / commit 层的租约绑定拒绝，含
  「提交已落地、应答丢失后的重报」——finish 已把租约推入终态）或协议版本
  低于下限——终态，不重试（#644 语义不变）。
- 其余 4xx（400 元数据畸形 / 缺 lease 头、401 token 失效、413 归档超限）：
  确定性判决，端点在这些判决前不写任何执行态；原样重报必然同判。
- 5xx / 网络错误（传输层 ``_request_with_retry`` 内层有界重试后抛
  RuntimeError）与 408 / 425 / 429（超时、过早、限流——语义上就是「稍后
  再试」）：瞬时或 Host 内部失败，租约持有期间持续退避重试（心跳续租），
  由 204 / 409 / ownership_lost 自然终止——**从不**把可交付结果判败：Host
  存活而 /result 持续失败（对象存储 / 磁盘 / DB 故障）时，判败等于把长时
  成功执行白跑。重试幂等：commit 的副作用全部绑定 lease
  （``completion.finish`` 在租约守卫下推进终态、``mark_done`` 按 lease_id
  关单），任何重报要么首次提交、要么吃 409，不会重复提交。

旧形态把「其余 4xx」与 409 一并当终态删 marker：Host 从未收到终态，租约
过期后 sweeper 重排，整次执行重跑、重跑又撞同一判决——全量重跑循环。现在
这些判决经**一次性**降级闸（``ReportDegradeGate``）改报诚实判败（failed
metadata，Host 记录显式失败并终结租约）；判败上报仍被拒才按终态删 marker。

主路径的归档上限预检（``declared_ceiling_rejection``）同属这一判决面：
prepare 备妥的归档超 Host 下发上限即诚实判败，不把注定 413 的归档送进
report 车道。
"""

from __future__ import annotations

from pathlib import Path

from worker.upload.embed_precheck import ARCHIVE_EMBED_DEFAULT_CEILING_BYTES
from worker.upload.result_metadata import failed_metadata, write_empty_archive
from worker.upload.task import UploadTask

# 「稍后再试」语义的 4xx：与 5xx 同归瞬时臂（持续重试），不是判决。
RETRYABLE_CLIENT_STATUSES = frozenset({408, 425, 429})


def is_transient_status(status_code: int) -> bool:
    """5xx 与 408 / 425 / 429：瞬时失败，租约持有期间持续重试。"""
    return status_code >= 500 or status_code in RETRYABLE_CLIENT_STATUSES


def is_verdict_rejection(status_code: int) -> bool:
    """409 与「稍后再试」之外的 4xx：确定性判决，原样重报必然同判。"""
    return (
        400 <= status_code < 500
        and status_code != 409
        and status_code not in RETRYABLE_CLIENT_STATUSES
    )


def ensure_submittable_archive(archive: Path, ceiling: int) -> None:
    """诚实判败通道的归档必须可提交（#755 codex R8 P2 对抗复审）：头溢出
    在任何大小检查之前抛出，原归档本身可能已超 Host 上限却从未过大小
    门禁——此时重报原归档只会吃 413、被本循环当终态删 marker。超限即
    回收成空归档（判败语义下证据让位于可提交性，同 prepare 失败臂）。"""
    if archive.is_file() and archive.stat().st_size > ceiling:
        write_empty_archive(archive)


def declared_ceiling_rejection(task: UploadTask, archive: Path) -> str | None:
    """主路径 prepare 后的归档上限预检：超 Host 经 claim 下发的
    ``max_archive_bytes`` 即回收空归档并返回判败原因（交给 failed_metadata）。

    只按 Host 实际下发值判定：未下发（旧 Host / 崩溃恢复的任务，
    ``max_archive_bytes == 0``）时本地不猜上限——64 MiB 默认可能低于 Host
    实际配置而误杀可交付结果，交给 Host 的 413 判决，由 report 循环的
    4xx 降级臂兜底。"""
    ceiling = task.max_archive_bytes
    archive_bytes = archive.stat().st_size if archive.is_file() else 0
    if ceiling <= 0 or archive_bytes <= ceiling:
        return None
    write_empty_archive(archive)
    return (
        f"result archive is {archive_bytes} bytes, over the {ceiling}-byte Host"
        f" archive ceiling; reporting the run failed instead of an undeliverable archive"
    )


class ReportDegradeGate:
    """report 循环对 4xx 判决的判败降级闸（瞬时失败从不降级）。

    降级把 failed metadata 挂回 ``task.prepared_metadata``（调用方从那里
    取下一次上报的载荷）。闸只开一次，唯一例外：首次降级时归档保留（非
    413 判决），判败重报又吃 413——Host 先验 metadata 再验 body 大小，
    未下发 ``max_archive_bytes``（旧 Host / 崩溃恢复）时 64 MiB 兜底可能
    高于 Host 实际上限——此时再清空归档重报一次。总重报次数仍有界（≤2）。"""

    def __init__(self, task: UploadTask, archive: Path) -> None:
        self._task = task
        self._archive = archive
        self.degraded = False
        self._archive_emptied = False

    def on_rejection(self, status_code: int, rejection: str) -> bool:
        """True = 已降级、应重报判败；False = 终态（409 / 非 4xx / 已降级过）。"""
        if not is_verdict_rejection(status_code):
            return False
        if self.degraded and (status_code != 413 or self._archive_emptied):
            return False
        reason = f"result report rejected by Host: {rejection}"
        # 413 清空归档；其余判决保留归档作证据，只按上限回收。
        if status_code == 413:
            write_empty_archive(self._archive)
            self._archive_emptied = True
        else:
            ceiling = self._task.max_archive_bytes or ARCHIVE_EMBED_DEFAULT_CEILING_BYTES
            ensure_submittable_archive(self._archive, ceiling)
        print(
            f"result report for {self._task.execution_id}: {reason}; reporting failed", flush=True
        )
        if not self.degraded:
            self.degraded = True
            self._task.prepared_metadata = failed_metadata(self._task, reason)
        return True
