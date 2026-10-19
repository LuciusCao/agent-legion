"""#1279 codex final P1：响应描述符快照的协议复核（repair/failed-retry TOCTOU）。

admission 后 complete 可把无 hash 行从 uploading/failed 落回 ready 并写入新
etag——此时描述符仍会被 v5 旧 Worker 无条件 GET（忽略增量字段），活 URL 再被
同尺寸覆盖就静默执行错误字节。本模块在描述符构造点对 < v6 Worker 且**无 hash
且有 etag** 的行（即本 PR 新增的 repair/complete 路径能产生的形态）直接拒绝：
batch claim 的 per-item 剔除走既有的 lease-expiry 重排队恢复，不是硬失败
（见 build_batch_claim_response）。hash 行恒免（sha256 兜底）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shared.material_cache import MaterializeError
from shared.protocol import MATERIAL_ETAG_PROTOCOL_VERSION


def assert_worker_etag_protocol(
    row: Mapping[str, Any], worker_protocol_version: int | None
) -> None:
    """无 hash 且有 etag 的行对 < v6 Worker 拒绝（见模块 docstring）。

    ``worker_protocol_version`` 必须实传（响应路径总能拿到）；None（非
    claim 路径的旧调用方）不做判定。
    """
    if (
        worker_protocol_version is not None
        and worker_protocol_version < MATERIAL_ETAG_PROTOCOL_VERSION
        and not str(row.get("content_hash") or "")
        and str(row.get("object_etag") or "")
    ):
        raise MaterializeError(
            f"material {row['id']} ETag verification requires worker protocol "
            f"v{MATERIAL_ETAG_PROTOCOL_VERSION}; upgrade the worker"
        )
