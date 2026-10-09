"""Worker 直传产物引用的契约面与结果归档清单的读回面（#755 codex P1）。

两半共享同一批校验原语，收在同一模块避免 routes ← agent_broker 的循环
import（routes/__init__ 会拉起 agent_workers → agent_result_commit）：

- ``parse_artifact_ref`` / ``MAX_OUTPUT_ARTIFACTS``：结果头清单的 ref 形态
  校验与条目上限（原 server/app/routes/agent_worker_result_refs.py，随
  本模块落地整体下沉；路由层 parse_result_metadata 从这里复用）。
- ``load_archived_output_artifacts``：v1 legacy 换轨形态（worker-v0.7.18
  及更早）的读回面——旧 Worker 结果头溢出时把完整直传 ref 清单写成归档
  首成员 ``result-output-artifacts.json``
  （shared/code_contract.RESULT_OUTPUT_ARTIFACTS_MEMBER），头里只留
  ``output_artifacts_in_archive`` 标记。#843 PR-2 起新 Worker（v2
  result.json）不再产生该形态，读侧保留旧 Worker 兼容窗。
- ``enrich_outcome_from_archived_manifest``：commit 层（agent_result_commit）
  见标记后的 outcome/record 改写，含读回失败的诚实判败转换。

Worker 归档是不可信输入：读取上限、条目数、名字与 ref 全部过与头部清单
同源的安全校验。
"""

from __future__ import annotations

import json
import logging
import re
import tarfile
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

from shared.code_contract import RESULT_OUTPUT_ARTIFACTS_FLAG, RESULT_OUTPUT_ARTIFACTS_MEMBER

logger = logging.getLogger(__name__)

_ARTIFACT_REF = re.compile(r"^sha256:[0-9a-f]{64}$")
_ARTIFACT_HASH = re.compile(r"^[0-9a-f]{64}$")
_MAX_STORAGE_KEY_CHARS = 1024

# 结果清单（头部或归档成员）的条目上限；路由层与归档读回面共用。
MAX_OUTPUT_ARTIFACTS = 128
# 清单成员的读取上限：128 条直传 ref ~25 KB，1 MiB 已是 40 倍余量——
# 超限即畸形归档，拒绝而非截断（半截 JSON 无法解析，截断无意义）。
_MAX_MANIFEST_BYTES = 1024 * 1024
# 读回失败的 error_message 截断口径与 parse_result_metadata 相同。
_MAX_ERROR_MESSAGE_CHARS = 4000


def parse_artifact_ref(ref: Any) -> str | dict[str, Any]:
    """One ``output_artifacts`` value: legacy CAS ref or object-storage ref.

    Legacy form: ``"sha256:<64 hex>"`` (returned as-is). Object-storage form
    (#160 D12): ``{"storage_key", "size_bytes", "content_hash"}`` — the key
    must stay inside the per-execution ``jobs-staging/`` prefix with no
    traversal (the Host promotes onto the authority key after verification),
    the size must be a non-negative int, and the hash is empty or 64
    lowercase hex.
    """
    if isinstance(ref, str):
        if not _ARTIFACT_REF.fullmatch(ref):
            raise ValueError("invalid output artifact reference")
        return ref
    if not isinstance(ref, dict):
        raise ValueError("invalid output artifact reference")
    storage_key = ref.get("storage_key")
    if not isinstance(storage_key, str) or len(storage_key) > _MAX_STORAGE_KEY_CHARS:
        raise ValueError("invalid output artifact storage key")
    key_path = PurePosixPath(storage_key)
    if key_path.is_absolute() or ".." in key_path.parts or key_path.parts[:1] != ("jobs-staging",):
        raise ValueError("invalid output artifact storage key")
    size_bytes = ref.get("size_bytes")
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
        raise ValueError("invalid output artifact size")
    content_hash = ref.get("content_hash", "")
    if not isinstance(content_hash, str) or (
        content_hash and not _ARTIFACT_HASH.fullmatch(content_hash)
    ):
        raise ValueError("invalid output artifact content hash")
    return {
        "storage_key": storage_key,
        "size_bytes": size_bytes,
        "content_hash": content_hash,
    }


def load_archived_output_artifacts(archive: Path) -> dict[str, Any]:
    """从结果归档读回直传产物清单；任何契约违反都 ValueError（带原因）。

    流式 ``r|gz`` 扫到清单成员即停（Worker 把它写在第一个，通常几 KB 即
    命中；找不到就扫完全流后报缺失）。校验面与头部清单同构：必须是 dict、
    条目数 ≤ ``MAX_OUTPUT_ARTIFACTS``、每个 name 非绝对路径且无 ``..``、
    每个 ref 过 parse_artifact_ref。
    """
    with tarfile.open(archive, "r|gz") as tar:
        for member in tar:
            if member.name != RESULT_OUTPUT_ARTIFACTS_MEMBER:
                continue
            if member.size > _MAX_MANIFEST_BYTES:
                raise ValueError("archived output artifacts manifest is too large")
            contents = tar.extractfile(member)
            if contents is None:
                raise ValueError("archived output artifacts manifest is not a file")
            with contents:
                payload = contents.read(_MAX_MANIFEST_BYTES + 1)
            if len(payload) > _MAX_MANIFEST_BYTES:
                raise ValueError("archived output artifacts manifest is too large")
            return _parse_manifest(payload)
    raise ValueError("archived output artifacts manifest member is missing")


def enrich_outcome_from_archived_manifest(
    outcome: Any, record: dict[str, Any], archive: Path
) -> Any:
    """commit 层的清单读回编排：outcome/record 改写 + 读回失败的诚实判败。

    ``outcome`` 是 frozen dataclass（AgentOutcome；本模块不 import 它以保持
    agent_broker → agent_control 的单向依赖，dataclasses.replace 按鸭子类型
    工作）。读回成功：outcome.output_artifacts 换成全集，record 同步——
    finish 侧零改动（空清单翻转、HEAD 校验、staged promote 全走既有路径）。
    读回失败：产物引用不可用，completed 的 run 改判 failed（沿用空清单翻转
    的语义钉子）；already-failed 臂保留原始失败签名（exit code + 诊断），
    只把读回失败追加进 error_message；cancelled 臂不翻转状态，同样在
    record 留一行痕迹（partial ref 全集随归档回收丢失，审计面不能零痕迹）。
    三条臂都把 record 的标记键归一为 False：commit 完成后归档即被回收，
    「清单在归档里」在持久化面上永不再真（#755 对抗复审 F2/F3）。
    版本偏斜说明：新 Worker + 不认识标记的旧 Host 走空清单翻转诚实判败，
    不会静默错。
    """
    try:
        parsed = load_archived_output_artifacts(archive)
    except Exception as exc:
        # #204 broad-except audit: Worker 归档是不可信输入，读回面的逃逸族
        # 混族——OSError（spool 文件不可读）、tarfile.TarError（坏 gzip/tar）、
        # ValueError（清单契约违反：缺成员/超限/坏 ref）。失败语义：读不回
        # 清单 = 产物引用不可用，completed 改判 failed（沿用空清单翻转的
        # 语义钉子），而非带着空 output_artifacts 判 completed。结果空间：
        # 仅本次 commit 的 outcome/record 被改写，finish 走既有失败路径；
        # cancelled 不翻转。日志保全：logger.exception 带堆栈。
        logger.exception("archived output artifacts manifest unreadable: %s", archive.name)
        note = f"archived output artifacts manifest is unreadable: {exc}"
        record[RESULT_OUTPUT_ARTIFACTS_FLAG] = False
        if outcome.status == "cancelled":
            record["error_message"] = f"{record.get('error_message', '')}\n{note}".strip()[
                :_MAX_ERROR_MESSAGE_CHARS
            ]
            return outcome
        if outcome.status == "failed":
            # #755 对抗复审 F1：保留原始失败签名（exit code + 诊断面），
            # 清单读回失败只追加说明——整体覆盖会让真实崩溃原因从结果面
            # 与 outcome_json 审计面同时消失。
            record["output_artifacts"] = {}
            record["error_message"] = f"{record.get('error_message', '')}\n{note}".strip()[
                :_MAX_ERROR_MESSAGE_CHARS
            ]
            return replace(outcome, error_message=record["error_message"], output_artifacts={})
        message = note[:_MAX_ERROR_MESSAGE_CHARS]
        record["status"] = "failed"
        record["exit_code"] = 1
        record["error_message"] = message
        record["output_artifacts"] = {}
        return replace(
            outcome, status="failed", exit_code=1, error_message=message, output_artifacts={}
        )
    record[RESULT_OUTPUT_ARTIFACTS_FLAG] = False
    record["output_artifacts"] = parsed
    return replace(outcome, output_artifacts=parsed)


def _parse_manifest(payload: bytes) -> dict[str, Any]:
    try:
        manifest = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("archived output artifacts manifest is not valid JSON") from exc
    if not isinstance(manifest, dict) or len(manifest) > MAX_OUTPUT_ARTIFACTS:
        raise ValueError("invalid archived output artifacts")
    parsed: dict[str, Any] = {}
    for name_raw, ref in manifest.items():
        name = str(name_raw)
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe archived output artifact name: {name!r}")
        parsed[name] = parse_artifact_ref(ref)
    return parsed
