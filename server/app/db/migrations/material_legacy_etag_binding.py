"""Schema v99: ``materials.legacy_etag_bound_at`` — 存量行 ETag 绑定时刻（#1316）。

v98 之前完成的无 hash ready 行 ``object_etag=''``，没有内容身份。#1316 在新
Host 启动时（及运维 CLI）按当前 HEAD 一次性为它们绑定 ETag（见
services/material_legacy_etag_binding.py），并在同一 CAS 里把绑定时刻落进
本列。本列只服务一个判定：旧 Worker（协议 < v6）门禁何时对这一行放行——
绑定时刻 + presign TTL + 时钟裕度之后，升级前签出的 PUT URL 必已全部过期，
旧 Worker 的无条件 GET 不再有被覆盖的通道（谓词见
jobs/queries/material_claim_gate.py）。

NULL = 不是 binder 绑定的行（新 complete 绑定 / 未绑定 / repair 之后）。
complete（mark_ready）与 repair 的 ready 分支都会清空本列。

Same guarded-ALTER home rule as v87+: the column lives ONLY here
(postgres_schema.sql sits at its budget ceiling), idempotent on replay, and
both install paths run the chain anyway.
"""

from __future__ import annotations

from typing import Any

_LEGACY_ETAG_BOUND_AT_DDL = """
alter table materials
  add column if not exists legacy_etag_bound_at timestamptz;
"""


def migrate_material_legacy_etag_binding(conn: Any) -> None:
    """Add the legacy-ETag binding instant column (v99, #1316); idempotent."""
    conn.execute(_LEGACY_ETAG_BOUND_AT_DDL)
