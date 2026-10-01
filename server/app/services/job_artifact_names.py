"""Artifact-name / job-id input whitelists (#631 攻击复审 M1/M2).

Split from job_query_presenters / job_artifacts for the architecture file
budget: the serve-side name whitelist and the job-id shape gate are pure
functions shared by the artifact read paths and the external-access service —
they live here so the pruning rules of the listing side and the rejection
rules of the download side keep ONE source of truth.
"""

from __future__ import annotations

from pathlib import PurePosixPath

# job_dir 里不是产物的子树：runs/ 是每节点的执行 run 目录（events.jsonl
# 等），点前缀目录是清理/解包暂存（.trash、.result-staging-*）。清单剪枝
# （artifact_names_deep）与 serve 侧拒绝（is_downloadable_artifact_name）
# 共用这一份名单（#631 攻击复审 M2：下载校验只做包含性、不认识 runs/，
# 会让「清单不列但可达」的不对称面存在）。
NON_ARTIFACT_DIR_NAMES = frozenset({"runs"})

# 产物名段白名单参数（#631 攻击复审 M1）：段长上限 200——远超合法产物文
# 件名（Windows 常见 255 字节 NAME_MAX 已含路径余量），又低于会让 lstat
# 抛 OSError ENAMETOOLONG 的长度（实测 300 字节单段即炸 500）。名字总长
# 不设独立上限：OS 路径上限由段数 × 段长自然约束，深子路径产物
# （reports/…）的合法深度不受影响。
MAX_ARTIFACT_NAME_SEGMENT_BYTES = 200


def is_downloadable_artifact_name(artifact_name: str) -> bool:
    """Serve-side name whitelist shared by the raw/text read paths.

    清单侧的剪枝规则在这里成为下载侧的拒绝规则：``runs/`` 前缀段与点前
    缀段（``.trash/``、``.result-staging-*/``）不是产物名，编码 NUL/控制
    字符、反斜杠（与 result_unpack / download_remote_artifact 同一规则，
    PurePosixPath 把它当普通字符）与超长段（会在文件系统调用里炸成 500
    而不是 4xx）一律拒绝。返回 False 时调用方按 InvalidOperationError
    （400）处理。
    """
    if (
        not artifact_name
        or "\\" in artifact_name
        or any(ord(ch) < 0x20 or ch == "\x7f" for ch in artifact_name)
    ):
        return False
    relative = PurePosixPath(artifact_name)
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        return False
    return all(
        part not in NON_ARTIFACT_DIR_NAMES
        and not part.startswith(".")
        and len(part.encode("utf-8")) <= MAX_ARTIFACT_NAME_SEGMENT_BYTES
        for part in relative.parts
    )


def is_plausible_job_id(job_id: str) -> bool:
    """Cheap input-shape gate for externally supplied job ids.

    #631 攻击复审 M1：``%00`` 进 job_id 会让 psycopg 在 SQL 参数化时抛
    ``DataError``（500，可远程触发的错误监控污染 + 500/404 侧信道）。
    合法 job id 是 ``{workspace}_{workflow_key}_{source_id}``（见
    ``jobs/queries`` 的 ``_job_id``），永远不含控制字符；NUL/控制字符
    在进任何 SQL/文件系统调用前拒绝（调用方按 400 处理）。
    """
    return bool(job_id) and not any(ord(ch) < 0x20 or ch == "\x7f" for ch in job_id)
