"""Schema v98: ``materials.object_etag`` — 材料对象 ETag（#1279 所有权协议）。

complete() 在既有 HEAD 核验上取 ``head.etag``，与 ``mark_ready`` 同事务落本列；
inline 文本与 demo seed 两条直插 ready 产线在 put 后 HEAD 取 ETag 一并落列。
消费侧（Host 物化 If-Match / Worker claim 描述符）以本列判等检出「complete 后
同尺寸静默覆盖」。存量行 default '' 是过渡态（codex r8：升级前完成、旧 PUT URL
仍在 1h 有效期内的无 hash 行仍处窗口内）——启动后台回填
（services/material_etag_backfill.py，由独立一次性后台线程
material_etag_binder 承载，启动即跑，不受 sweeper_enabled 门控）逐行
HEAD 取当前 ETag、CAS 落库收口；回填完成前这些行由 fail-closed（Host 物化）
与门禁挂起（Worker claim 拦全部）挡住（codex r9），不再放行。

Same guarded-ALTER home rule as v87+: the column lives ONLY here
(postgres_schema.sql sits at its budget ceiling), idempotent on replay, and
both install paths run the chain anyway.
"""

from __future__ import annotations

from typing import Any

_OBJECT_ETAG_DDL = """
alter table materials
  add column if not exists object_etag text not null default '';
"""


def migrate_material_object_etag(conn: Any) -> None:
    """Add the object-ETag column (v98, #1279); idempotent."""
    conn.execute(_OBJECT_ETAG_DDL)
