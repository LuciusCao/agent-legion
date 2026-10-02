"""结果头溢出的直传清单归档通道（#755 codex P1）。

结果头（X-Agent-Result，14 KiB 预算）装不下直传 dict ref 清单时，旧回退
会把产物重新经 legacy ``/api/artifacts`` CAS 通道上传（违反
EXEC-ARTIFACT-WORKER-001 的 presigned-only 约束，且字节双传）。新协议：
产物字节不动（已在 S3），把完整 ``{"name": ref}`` 清单作为 tar 首成员
``result-output-artifacts.json`` 写进结果归档，头里只带
``output_artifacts_in_archive`` 布尔标记；Host 侧 commit 层从归档读回清单
（server/app/agent_broker/result_output_manifest.py）。
"""

from __future__ import annotations

import io
import json
import os
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from shared.code_contract import RESULT_OUTPUT_ARTIFACTS_MEMBER


def embed_output_artifacts_manifest(
    archive: Path, artifacts: dict[str, Any], expected_outputs: tuple[str, ...] | list[str]
) -> None:
    """把直传产物清单作为首成员写进结果归档（同目录临时文件 + os.replace 原子替换）。

    溢出信号只在直传 ref 形态抛出，此时必有直传 ref：空清单或任何 ref 不是
    dict 形态都是契约违例（ValueError）。成员名与 expected_outputs 碰撞同理
    （实际上不可能——expected outputs 是节点业务产物名；防御性检查，调用方
    转诚实判败）。除清单成员外原归档字节原样复制（流式 ``r|gz`` → ``w|gz``；
    产物字节不在归档内，体量即 run_dir 日志级）。成员固定写在最前：Host 侧
    流式扫描几 KB 即命中，不必解完整归档。
    """
    if not artifacts or not all(isinstance(ref, dict) for ref in artifacts.values()):
        raise ValueError("output artifacts manifest requires direct-upload dict refs")
    if RESULT_OUTPUT_ARTIFACTS_MEMBER in set(expected_outputs):
        raise ValueError(f"{RESULT_OUTPUT_ARTIFACTS_MEMBER} collides with an expected output")
    payload = json.dumps(artifacts, ensure_ascii=False).encode("utf-8")
    descriptor, staging = tempfile.mkstemp(
        dir=archive.parent, prefix=".result-manifest-", suffix=".tar.gz"
    )
    staging_path = Path(staging)
    try:
        with (
            os.fdopen(descriptor, "wb") as raw,
            tarfile.open(archive, "r|gz") as src,
            tarfile.open(fileobj=raw, mode="w|gz") as dst,
        ):
            info = tarfile.TarInfo(RESULT_OUTPUT_ARTIFACTS_MEMBER)
            info.size = len(payload)
            dst.addfile(info, io.BytesIO(payload))
            for member in src:
                # 只对常规文件成员取数据面：流模式下对 symlink/hardlink 成员
                # 调 extractfile 抛 StreamError（#755 对抗复审 P2-1——run_dir
                # 在 agent 工作目录树内，链接成员不可排除；extractfile 对目录
                # 成员返回 None）。链接成员按引用原样复制，保真度不丢。
                if not member.isfile():
                    dst.addfile(member)
                    continue
                contents = src.extractfile(member)
                if contents is None:  # 防御：isfile 成员在此必然有数据面
                    dst.addfile(member)
                else:
                    with contents:
                        dst.addfile(member, contents)
        os.replace(staging_path, archive)
    except BaseException:
        # #204 broad-except audit (BaseException)：staging 清理守卫而非吞
        # 异常——bare raise 原样上抛，调用方（report.py 的溢出臂）把
        # OSError/tarfile.TarError/ValueError 转诚实判败。同
        # _persist_stderr_tail 的纪律：staging 与归档同目录，替换失败也不
        # 能把半成品留在执行目录里（会随归档外发）。
        staging_path.unlink(missing_ok=True)
        raise
