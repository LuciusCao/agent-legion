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
  RuntimeError）：瞬时或 Host 内部失败。重试幂等：commit 的副作用全部绑定
  lease（``completion.finish`` 在租约守卫下推进终态、``mark_done`` 按
  lease_id 关单），任何重报要么首次提交、要么吃 409，不会重复提交。

旧形态把「其余 4xx」与 409 一并当终态删 marker、瞬时失败无界重试：Host
从未收到终态，租约过期后 sweeper 重排，整次执行重跑、重跑又撞同一判决
——全量重跑循环。现在两条臂共用**一次性**降级闸（``ReportDegradeGate``）
降级为诚实判败上报（failed metadata，Host 记录显式失败并终结租约）；降级
上报本身仍被拒 / 耗尽才放弃（4xx → 删 marker；瞬时 → 保留 marker 交给
下次启动 restore）。

主路径的归档上限预检（``declared_ceiling_rejection``）同属这一判决面：
prepare 备妥的归档超 Host 下发上限即诚实判败，不把注定 413 的归档送进
report 车道。
"""

from __future__ import annotations

from pathlib import Path

from worker.upload.embed_precheck import ARCHIVE_EMBED_DEFAULT_CEILING_BYTES
from worker.upload.result_metadata import failed_metadata, write_empty_archive
from worker.upload.task import UploadTask

# 外层瞬时失败轮数上限：每轮是传输层一次完整的内层重试（3 次尝试），轮间
# 退避按 report 循环的 base 2s 翻倍、60s 封顶——20 轮的退避合计约 16 分钟
# （不含请求本身耗时），覆盖 Host 滚动重启 / 短时 DB 故障这类可自愈窗口；
# 退避期间心跳恢复，租约不会因等待而过期。超出即判定为确定性失败。
REPORT_TRANSIENT_MAX_ROUNDS = 20


def is_verdict_rejection(status_code: int) -> bool:
    """409 之外的 4xx：确定性判决，原样重报必然同判。"""
    return 400 <= status_code < 500 and status_code != 409


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
    """report 循环的一次性判败降级闸：4xx 判决与瞬时失败耗尽共用。

    降级把 failed metadata 挂回 ``task.prepared_metadata``（调用方从那里
    取下一次上报的载荷）。"""

    def __init__(self, task: UploadTask, archive: Path) -> None:
        self._task = task
        self._archive = archive
        self.degraded = False
        self._transient_rounds = 0

    def on_transient(self, exc: Exception) -> bool:
        """记一轮瞬时失败；False = 判败上报也已耗尽，放弃本轮投递。"""
        self._transient_rounds += 1
        if self._transient_rounds < REPORT_TRANSIENT_MAX_ROUNDS:
            return True
        if self.degraded:
            print(f"result report gave up for {self._task.execution_id}: {exc}", flush=True)
            return False
        # 有界重试耗尽：Host 持续失败已不是可自愈的瞬时窗口。归档清空——
        # 其内容本身可能就是 Host 端失败的原因。
        reason = f"result report failed after {REPORT_TRANSIENT_MAX_ROUNDS} attempts: {exc}"
        self._degrade(reason, empty_archive=True)
        return True

    def on_rejection(self, status_code: int, rejection: str) -> bool:
        """True = 已降级、应重报判败；False = 终态（409 / 非 4xx / 已降级过）。"""
        if not is_verdict_rejection(status_code) or self.degraded:
            return False
        # 413 清空归档；其余判决保留归档作证据，只按上限回收。
        self._degrade(
            f"result report rejected by Host: {rejection}", empty_archive=status_code == 413
        )
        return True

    def _degrade(self, reason: str, *, empty_archive: bool) -> None:
        # 判败上报拿到一份完整的瞬时重试预算（不继承降级前已耗掉的轮数）。
        self.degraded, self._transient_rounds = True, 0
        if empty_archive:
            write_empty_archive(self._archive)
        else:
            ceiling = self._task.max_archive_bytes or ARCHIVE_EMBED_DEFAULT_CEILING_BYTES
            ensure_submittable_archive(self._archive, ceiling)
        print(
            f"result report for {self._task.execution_id}: {reason}; reporting failed", flush=True
        )
        self._task.prepared_metadata = failed_metadata(self._task, reason)
