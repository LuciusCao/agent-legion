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
    前移留待 follow-up）。"""
    for name in map(str, manifest.get("expected_outputs") or []):
        if name in RESERVED_RESULT_ARCHIVE_MEMBERS:
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
