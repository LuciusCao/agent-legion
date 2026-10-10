"""Enqueue-time manifest guard for the Agent execution queue.

A request whose frozen manifest carries no routable provider/model can never
match a real Worker declaration: it would sit at the queue head forever,
silently blocking the workspace behind it (2026-08-01 incident, issue #13).
The broker rejects such manifests at enqueue instead — the producer surfaces
a node failure with an actionable message rather than a scheduling deadlock.

Also owns the canonical SQL expression for a request's shard identity (#401):
the one-active-request unique index (schema v79) and the claim side's
active-request gate share it, so index dedup and business dedup can never
drift into two conventions.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from server.app.db.migrations.shard_identity_index import SHARD_IDENTITY_SQL
from shared.code_contract import RESERVED_RESULT_ARCHIVE_MEMBERS

__all__ = [
    "PLACEHOLDER_MODELS",
    "SHARD_IDENTITY_SQL",
    "require_routable_execution",
    "require_unreserved_output_names",
]

# Template placeholders, not routable models: `your-model` is the historical
# config/workflow.yaml default that deadlocked the production queue.
PLACEHOLDER_MODELS = frozenset({"your-model"})


def require_unreserved_output_names(manifest: Mapping[str, Any]) -> None:
    """#843 评审 P1（c 层）：expected output 命中结果归档保留成员名即拒绝。

    命中 ``result.json`` / ``node.log`` / ``result-output-artifacts.json``
    的产物名会在归档与提升面与协议成员碰撞（v2 元数据换写吞掉真产物、
    node.log 与捕获日志双写互覆）——入队即节点失败并点名冲突名，同
    require_routable_execution 的 #13 fail-fast 形态（跑时守卫；发布侧
    前移留待 follow-up）。

    命名空间契约（一句话模型）：expected output 的**落盘路径**——
    ``PurePosixPath(name).as_posix()`` 归一化后的形态——不得等于任一
    保留成员名；比对是归一化后的**精确等值**，非前缀、非子串（
    ``sub/result.json`` 是独立路径，合法）。#1164 收口根因：原实现按
    原始字符串精确比对，模型里「名字」与「落盘路径」被当成同一个东西
    ——``./result.json`` / ``.//result.json`` 是不同字符串、同一落盘
    路径，穿过了字符串相等却命中归一化路径碰撞（提升守卫同款漏洞，
    staging 元数据成员会被静默提升成产物）。绝对 / ``..`` 形态不在此
    判（unsafe 家族由提升守卫的既有拒绝收口）。"""
    for name in map(str, manifest.get("expected_outputs") or []):
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts:
            continue
        if relative.as_posix() in RESERVED_RESULT_ARCHIVE_MEMBERS:
            raise ValueError(
                f"expected output {name!r} collides with the reserved result-archive"
                " member; rename the node output"
            )


def require_routable_execution(manifest: Mapping[str, Any]) -> None:
    """Fail fast when the frozen manifest carries no routable provider/model."""
    if manifest.get("kind") == "code":
        # Code payloads carry no provider/model; routability is the code text
        # itself (hash-pinned bundle) plus the capability declaration.
        if not str(manifest.get("capability") or "") or not str(manifest.get("code_hash") or ""):
            raise ValueError("code request manifest requires a capability and a code_hash")
        return
    execution = manifest.get("execution") or {}
    provider = str(execution.get("provider") or "")
    model = str(execution.get("model") or "")
    if not provider or not model or model in PLACEHOLDER_MODELS:
        raise ValueError(
            f"Agent request manifest has unresolved provider/model "
            f"{provider!r}/{model!r}: no Worker could ever claim it; declare them "
            "via the node execution settings or the workspace defaults"
        )
