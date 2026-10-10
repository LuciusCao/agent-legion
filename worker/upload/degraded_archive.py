"""降级仍可上报的结果归档与判败载荷（#959 / #1168 / #1169）。

从 ``worker/upload/result_metadata.py`` 拆出并收口的写面：上报链路的一切
诚实判败形态（prepare 失败臂、CAS 4xx 终态、report 4xx 降级闸、v2 终点
finalize 拒写）都汇聚到本模块——判败可以丢证据，**绝不能丢可提交性**：

- ``failed_metadata``：判败上报的统一载荷（status/exit_code 判定字段 +
  截断后的 error_message）。
- ``write_empty_archive``：body 空归档（v2 判败降级的回收目标），写前重建
  父目录（#1168 P1：execution_dir 被 agent 整目录自删时父目录已不存在，
  直接写会抛 FileNotFoundError 逃出失败臂——bulk 车道异常退出、failed
  结果报不上、卡到租约过期重跑，正是 #1147 的目标场景）。
- ``write_metadata_only_archive``：仅含 ``result.json`` 成员的 v2 回收归档，
  带 ``max_archive_bytes`` 上限时按实际 gzip 体积自适应裁剪非判定字段
  （command 清空、error_message 递减截断，保 status/exit_code）——修复前
  1 KiB 配置 + 4000 字符高熵 error_message 的形态仍超限、未复测就覆写，
  Host 持续 413、降级闸第二次同形超限即终态删 marker，结果丢、租约重跑
  （#1169 P2）。原子替换（同目录 staging + ``os.replace``）。
- ``write_degraded_empty_archive``：失败臂的最终兜底写入器——execution_dir
  不可写（EACCES/EROFS 族）时把空归档落进 state 目录取证结构（work_root
  之外），结果仍可上报；两处都写不进才上抛（崩溃-恢复语义兜底）。
"""

from __future__ import annotations

import io
import json
import os
import tarfile
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shared.code_contract import RESULT_METADATA_MEMBER
from worker import state_evidence
from worker.upload.result_metadata import MAX_ERROR_MESSAGE_CHARS

if TYPE_CHECKING:
    from worker.upload.task import UploadTask

# error_message 裁剪的递减 cap 序列（#1169）：从 2 KiB 对半降到 0，每档重建
# 归档实测 gzip 体积；判定字段（status/exit_code/清单）恒保留。
_CEILING_MESSAGE_CAPS = (2048, 1024, 512, 256, 128, 64, 0)


def failed_metadata(task: UploadTask, error_message: str) -> dict[str, Any]:
    """failed 上报的统一载荷（prepare 失败 / CAS 4xx 终态 / report 降级共用）。"""
    return {
        "status": "failed",
        "exit_code": 1,
        "error_message": error_message[:MAX_ERROR_MESSAGE_CHARS],
        "command": list(task.command),
        "output_artifacts": {},
    }


def write_empty_archive(archive: Path) -> None:
    """空 body 归档（v2 判败降级的回收目标；元数据成员由 bulk 车道终点的
    finalize 写入）。写前 ``mkdir(parents=True, exist_ok=True)``：#1168 P1
    的 execution_dir 整体消失形态（agent 自删）下父目录不存在，tarfile.open
    不会自建目录、直接抛 FileNotFoundError。"""
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "w:gz"):
        pass


def write_metadata_only_archive(
    archive: Path, metadata: dict[str, Any], max_bytes: int = 0
) -> None:
    """覆写为仅含 result.json 成员的归档（v2 判败降级的回收目标）。

    413 / 换写拒写等「归档不可提交」臂把证据归档回收成本形态：判败
    metadata 随首成员交付（否则 Host 400 缺成员）。``max_bytes`` 非 0 时按
    staging 实际 gzip 体积对上限复测，超限先裁剪非判定字段（command 清空、
    error_message 按 #1169 递减 cap 截断——判定字段 status/exit_code/
    output_artifacts 恒保留），裁到限内才原子替换；仍超限（理论上限：
    判定字段 + tar/gz 开销 < 300B，仅天花板形上限可触发）落盘超限形态并
    记日志。staging 与归档同目录，替换失败不留半成品在执行目录。"""
    data = _metadata_only_bytes(metadata)
    if max_bytes and len(data) > max_bytes:
        data = _metadata_only_bytes(_fit_ceiling(metadata, max_bytes))
    archive.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staging = tempfile.mkstemp(
        dir=archive.parent, prefix=".degraded-archive-", suffix=".tar.gz"
    )
    try:
        with os.fdopen(descriptor, "wb") as raw:
            raw.write(data)
        os.replace(staging, archive)
    except BaseException:
        # #204 broad-except audit (BaseException)：staging 清理守卫而非吞
        # 异常——bare raise 原样上抛，调用方按自身臂位处置（report 降级臂
        # 吞、finalize 判败臂转 failed 上报）。staging 与归档同目录，替换
        # 失败也不能把半成品留在执行目录（会随归档外发）。
        with suppress(OSError):
            os.unlink(staging)
        raise


def write_degraded_empty_archive(task: UploadTask) -> Path:
    """失败臂的空归档写入器（#1168 P1）：返回可上报归档的落盘路径。

    首选 execution_dir（常规路径，归档随目录收尾清理）；该目录不可写
    （EACCES/EROFS 族——execution_dir 整体消失时 ``write_empty_archive``
    的 mkdir 会重建它，走到这里的是目录存在但不可写的形态）时立即重试
    一次，仍失败则把归档落进 state 目录取证结构（``incident_dir`` 之内、
    work_root 之外——report 车道只按路径读字节，不关心归档住在哪；无
    TTL 的 evidence 语义顺带覆盖滞留归档的留存）。两处都写不进才把最后
    的 OSError 上抛：崩溃-恢复语义兜底（marker 留给下次启动 restore），
    强于带着不存在的归档路径进 report 车道空转重试。"""
    archive = task.execution_dir / "result.tar.gz"
    # 合成初值：两条主路径都成功时函数早已 return，走到 raise 的唯一通路
    # 是两处都写失败（真实异常会覆盖初值）。
    failure: OSError = OSError("degraded result archive could not be written")
    for delay in (0.0, 0.05):
        time.sleep(delay)
        try:
            write_empty_archive(archive)
            return archive
        except OSError as exc:
            failure = exc
    incident = state_evidence.incident_dir(task.execution_id, task.node_key)
    if incident is not None:
        stranded = incident / "result.tar.gz"
        try:
            write_empty_archive(stranded)
        except OSError as exc:
            failure = exc
        else:
            print(f"degraded result archive stranded outside work root: {stranded}", flush=True)
            return stranded
    raise failure


def _metadata_only_bytes(metadata: dict[str, Any]) -> bytes:
    """仅含 result.json 成员的归档字节（内存内构建——判败载荷是 KB 级）。"""
    payload = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo(RESULT_METADATA_MEMBER)
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _fit_ceiling(metadata: dict[str, Any], max_bytes: int) -> dict[str, Any]:
    """裁剪非判定字段直到 metadata-only 归档落在 ``max_bytes`` 内。

    高熵 error_message（413 回显的 verdict 正文、provider 报错回显）几乎
    不可压缩——gzip 实测体积是唯一可信口径，故每档 cap 重建归档实测而非
    估算。返回裁剪副本；调用方对返回形态照常实测（本函数返回值可能仍超
    限：判定字段 + tar/gz 开销的地板）。"""
    slim = dict(metadata)
    slim["command"] = []
    message = str(slim.get("error_message") or "")
    for cap in _CEILING_MESSAGE_CAPS:
        slim["error_message"] = message[:cap]
        if len(_metadata_only_bytes(slim)) <= max_bytes:
            return slim
    print(
        f"degraded metadata-only archive still over the {max_bytes}-byte ceiling"
        f" after trimming; writing it anyway",
        flush=True,
    )
    return slim
