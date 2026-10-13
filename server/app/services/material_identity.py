"""#1279 codex r9：材料身份守卫（消费侧 fail-closed）。

统一不变量：无 hash 材料的字节任何时候都不能在没有已验证身份锚的情况下
被消费（锚 = content_hash，或静止期绑定的 etag——状态机模型见
docs/architecture/materials-and-runs-design.md §6.4.1）。etag='' 的 ready
无 hash 行 = 身份未建立（存量行启动回填在途）——Host 物化不过 claim
门禁，这里 fail-closed；可重试（binder 绑定后即恢复）。hash 行不受影响
（sha256 兜底）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shared.material_cache import MaterializeError


def assert_material_identity_ready(row: Mapping[str, Any]) -> None:
    """无 hash 行必须带已建立的 etag 身份才可消费（见模块 docstring）。"""
    if not str(row.get("content_hash") or "") and not str(row.get("object_etag") or ""):
        raise MaterializeError(
            f"material {row.get('id')} identity is not yet established "
            "(startup etag backfill in flight); retry shortly"
        )
