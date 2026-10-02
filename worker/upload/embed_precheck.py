"""直传 → 归档内嵌换轨的体积闸门（#755 codex P1 / P2-1）。

DirectUploadError 回退臂在清规格重跑 prepare 之前先过换轨预检：内嵌总量超
「Host 实际上限 − 有效余量」时不换轨——重内嵌只会把大产出人群送进 Host
413 → 丢结果 → 租约过期全量重跑（每轮同样 413）。有效余量是上限的函数
（embed_safety_margin，#755 codex P2-1：小上限是合法配置，固定余量与可
配置上限直接做减法会在小上限下误杀任何产物）；预检是优化不是裁判，余量
带内的误放行由换轨重备后的 re-stat 兜底（embed_restast_rejection）。上限
来自 claim 下发（Host 实例设置 agent_workers.max_archive_bytes，经
agent_worker_claim_response 内存态注入，不持久化），旧 Host 未下发时
（UploadTask.max_archive_bytes == 0）回落 64 MiB 默认。
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from shared.code_contract import CODE_RESULT_LOG_MEMBER
from worker.upload.result_metadata import write_empty_archive
from worker.upload.task import UploadTask

# 默认上限与 server/app/configuration/executor_runtime.py 的同值默认对齐；
# 余量吸收 tar 头/gzip 开销——预检口径是未压缩字节，产物载荷占主导。
ARCHIVE_EMBED_DEFAULT_CEILING_BYTES = 64 * 1024 * 1024
EMBED_SAFETY_MARGIN_BYTES = 1024 * 1024


def embed_safety_margin(ceiling: int) -> int:
    """有效余量是上限的函数（#755 codex P2-1）：上限来自实例设置
    ``agent_workers.max_archive_bytes``（``gt=0`` 即合法，小上限是合法
    配置），固定 1 MiB 余量与可配置上限直接做减法，小上限下
    ``ceiling - margin`` 为负——几十字节的产物也必被预检拒绝，presigned
    PUT 一次暂时失败就把装得进 Host 上限的结果直接判 failed。余量按
    min(固定余量, 上限/4) 收缩：默认 64 MiB 上限行为不变，任何合法上限
    下预算恒为正（≥ 3/4 上限）。预检是优化不是裁判：余量带内的误放行
    由换轨后 re-stat 兜底（queue.py 的 DirectUploadError 臂），不可误杀。"""
    return min(EMBED_SAFETY_MARGIN_BYTES, ceiling // 4)


def embedded_artifacts_bytes(task: UploadTask) -> float:
    """归档内嵌会打包进 tar 的字节总量：expected_outputs 产物（未压缩口径
    ——gzip 对二进制不可假设，产物载荷占主导）+ run_dir 实测（换轨判定发生
    在 prepare 之后，events 已压缩，stat 即可；events/stderr 锚点一并入
    tar，MB 级，不计则余量被静默吃光）+ code 车道的 node.log（写在
    execution_dir 根而非 run_dir，是沙箱 stdout/stderr 的无上限捕获，可以
    是 tar 的最大成员；agent 车道该文件不存在，与归档侧的 is_file 判定同型
    跳过）。FileNotFoundError 的 expected output 按 0 字节计——缺席的产物
    不内嵌任何字节（该 run 反正会被 Host 判 Missing outputs），把它当
    +inf 会把「直传失败 + 产物缺失」误导成体积超限（#755 对抗复审 P3）；
    其他 stat 失败按 +inf——大小未知即拒绝换轨。"""
    job_dir = task.execution_dir / "job"
    total = 0.0
    for name in task.expected_outputs:
        try:
            total += (job_dir / PurePosixPath(name)).stat().st_size
        except FileNotFoundError:
            continue
        except OSError:
            return float("inf")
    run_dir = job_dir / "runs" / task.node_key / "worker"
    try:
        total += sum(entry.stat().st_size for entry in run_dir.rglob("*") if entry.is_file())
    except OSError:
        return float("inf")
    node_log = task.execution_dir / CODE_RESULT_LOG_MEMBER
    try:
        if node_log.is_file():
            total += node_log.stat().st_size
    except OSError:
        return float("inf")
    return total


def embed_switch_rejection(task: UploadTask) -> str | None:
    """换轨判定：None = 可以换轨；否则是判败原因（交给 failed_metadata）。"""
    embedded_bytes = embedded_artifacts_bytes(task)
    ceiling = task.max_archive_bytes or ARCHIVE_EMBED_DEFAULT_CEILING_BYTES
    margin = embed_safety_margin(ceiling)
    if embedded_bytes <= ceiling - margin:
        return None
    detail = (
        "an expected output or run-dir file could not be stat'ed"
        if embedded_bytes == float("inf")
        else f"embedded payload totals {int(embedded_bytes)} bytes"
    )
    return (
        f"{detail}: embedded payload exceeds the archive-embed ceiling"
        f" budget ({ceiling} bytes Host ceiling less"
        f" {margin} bytes safety margin); cannot switch"
        f" to the archive-embedded channel"
    )


def embed_restast_rejection(task: UploadTask, archive: Path) -> str | None:
    """换轨重备后的 re-stat 兜底（#755 codex P2-1）：预检是未压缩口径的
    优化放行（小上限下余量按上限比例收缩，tar/gzip 开销占比不可忽略），
    换轨重备的归档仍可能超 Host 上限——重报大归档只会吃 413 被 report
    循环当终态删 marker。超限先把归档回收成可提交体积（空归档，判败语义
    下证据让位于可提交性，同 report.py 的 embed 超限臂）并返回判败原因
    （交给 failed_metadata）；未超限返回 None。"""
    ceiling = task.max_archive_bytes or ARCHIVE_EMBED_DEFAULT_CEILING_BYTES
    archive_bytes = archive.stat().st_size if archive.is_file() else 0
    if archive_bytes <= ceiling:
        return None
    write_empty_archive(archive)
    return (
        f"archive-embedded fallback produced a {archive_bytes}-byte archive,"
        f" over the {ceiling}-byte Host archive ceiling; cannot deliver"
        f" the result archive"
    )
