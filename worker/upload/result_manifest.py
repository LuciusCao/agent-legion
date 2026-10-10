"""结果元数据 v2 归档成员写入面（#843 PR-2，Worker 写侧）。

v2 形态（请求头 ``X-Agent-Result-Format: 2``）：结果元数据 JSON（含完整
output_artifacts 清单）写成结果归档的保留首成员 ``result.json``
（shared/code_contract.RESULT_METADATA_MEMBER，UTF-8 JSON 文本）。写入时点
在 bulk 车道终点——产物引用（presigned dict ref / CAS 字符串 ref）此时才
终态，prepare 阶段构建的 body 归档（产物 + run_dir / node.log）尚无元数据
成员；``finalize_result_metadata`` 把终态 metadata 以「首成员 + 流式复制
既有成员」的原子替换写入，产物字节零重传。v1 时代的换轨成员
``result-output-artifacts.json`` 与 ``output_artifacts_in_archive`` 标记不再
产生：清单整体留在 result.json 里，v2 契约（PR-1 评审 P3-2）明文禁止
payload 携带该标记。

大小治理：v2 无头预算，metadata 受归档单成员体积约束——``max_archive_bytes``
（claim 下发）是唯一大小门；带上限调用时按 staging 实际大小拒写（原归档
不动），调用方走既有诚实判败通道。判败回收（下方拒写臂）的口径：
正值按值、0（未下发）按协议下限 ``MIN_RESULT_ARCHIVE_BYTES``——与
``report_policy`` 的两个回收位同口径（#1174 二轮，矩阵见其模块 docstring）。
"""

from __future__ import annotations

import io
import json
import os
import tarfile
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shared.code_contract import RESULT_METADATA_MEMBER
from worker.upload.degraded_archive import failed_metadata, write_metadata_only_archive
from worker.upload.task import degrade_ceiling

if TYPE_CHECKING:
    from worker.upload.task import UploadTask


class ResultMetadataOverCeiling(ValueError):
    """换写后的归档超 Host 归档上限（claim 下发的 max_archive_bytes）。

    staging 流式写完后、原子替换前按实际大小拒写，原归档字节未动（证据
    保全、仍是可提交体积），调用方走诚实判败通道，而不是重报大归档吃
    413 后被当终态删 marker。继承 ValueError，与既有契约违例同族。"""


def embed_result_metadata(
    archive: Path,
    metadata: dict[str, Any],
    max_bytes: int = 0,
) -> None:
    """把 metadata 写成结果归档首成员 ``result.json``（同目录临时文件 +
    os.replace 原子替换）。

    既有成员流式复制（``r|gz`` → ``w|gz``）保持原样（非常规成员按引用
    保真——流模式 extractfile 只对常规文件成员有效）；已存在的
    ``result.json`` 成员跳过（换写语义：判败降级重写新 metadata 时旧成员
    不得残留，Host 读回取首个命中）。成员固定写在最前：Host 侧流式扫描
    即刻命中，不必解完整归档。``max_bytes`` 非 0 时是 Host 归档上限：
    staging 写完后、替换前按实际大小校验，超限抛
    ``ResultMetadataOverCeiling``，原归档保持未动。
    """
    payload = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
    descriptor, staging = tempfile.mkstemp(
        dir=archive.parent, prefix=".result-metadata-", suffix=".tar.gz"
    )
    staging_path = Path(staging)
    try:
        with (
            os.fdopen(descriptor, "wb") as raw,
            tarfile.open(archive, "r|gz") as src,
            tarfile.open(fileobj=raw, mode="w|gz") as dst,
        ):
            info = tarfile.TarInfo(RESULT_METADATA_MEMBER)
            info.size = len(payload)
            dst.addfile(info, io.BytesIO(payload))
            for member in src:
                if member.name == RESULT_METADATA_MEMBER:
                    continue
                if not member.isfile():
                    dst.addfile(member)
                    continue
                if (contents := src.extractfile(member)) is None:  # isfile 必有数据面
                    dst.addfile(member)
                else:
                    with contents:
                        dst.addfile(member, contents)
        if max_bytes and (rewritten_size := staging_path.stat().st_size) > max_bytes:
            raise ResultMetadataOverCeiling(
                f"result archive with the embedded result.json member is"
                f" {rewritten_size} bytes, over the {max_bytes}-byte Host"
                f" archive ceiling; original archive left untouched"
            )
        os.replace(staging_path, archive)
    except BaseException:
        # #204 broad-except audit (BaseException)：staging 清理守卫而非吞
        # 异常——bare raise 原样上抛，调用方把 OSError / TarError /
        # ValueError 族转诚实判败。staging 与归档同目录，替换失败也不能
        # 把半成品留在执行目录里（会随归档外发）。
        staging_path.unlink(missing_ok=True)
        raise


def finalize_result_metadata(
    task: UploadTask, metadata: dict[str, Any], archive: Path
) -> tuple[dict[str, Any], Path]:
    """bulk 车道终点的 v2 落盘：终态 metadata（含产物清单）写成归档首成员。

    产物引用在 artifact 上传完成后才终态，故该步在 ``_bulk_transfer`` 尾
    （task.prepared_metadata / prepared_archive 挂载前）执行，此后归档即
    最终形态（report 车道原样发送）。超限（罕见形态：body 贴着上限、
    metadata 把它推过）或写失败（IO/压缩）时诚实判败：拒写（原归档不动
    由 embed 的 staging 替换保证），随后回收成仅含判败 metadata 的可提交
    归档（失败原因随 error_message 上报，同 prepare 降级臂的观测纪律）；
    判败语义下证据让位于可提交性。心跳纪律：本步在 bulk 车道执行（心跳
    仍武装），重写窗口不产生 #1098 形态的租约空窗。

    保留成员碰撞守卫（#843 评审 P1，v1 旧 embed 同形）：expected output
    命中 ``result.json`` 时，body 归档里该成员是真产物字节，而换写循环会
    跳过它、把元数据写成唯一 result.json——Host 提升面再把这份元数据当
    产物落进 job_dir，真产物被静默替换。此处先拒绝（ValueError → 下方
    判败臂），真产物让位于诚实判败上报；上游入队守卫（manifest_guard）
    在更早一层已把该形态拦成节点失败。"""
    try:
        if RESULT_METADATA_MEMBER in task.expected_outputs:
            raise ValueError(f"{RESULT_METADATA_MEMBER} collides with an expected output")
        embed_result_metadata(archive, metadata, max_bytes=task.max_archive_bytes)
    except (ResultMetadataOverCeiling, OSError, tarfile.TarError, ValueError) as exc:
        failed = failed_metadata(task, f"result metadata finalize failed: {exc}")
        # #1169：判败回收同样受 claim 上限约束——metadata-only 归档按上限
        # 自适应裁剪（command 清空 / error_message 截断），重报不再吃 413。
        # #1174 二轮：0 值（未下发）按协议下限裁（degrade_ceiling 单一
        # 口径），与 report_policy 两个回收位同口径（矩阵见其模块 docstring）。
        write_metadata_only_archive(archive, failed, degrade_ceiling(task))
        return failed, archive
    return metadata, archive
