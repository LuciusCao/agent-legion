"""Schema v98: ``materials.object_etag`` — 材料对象 ETag（#1279 所有权协议）。

complete() 在既有 HEAD 核验上取 ``head.etag``，与 ``mark_ready`` 同事务落本列；
inline 文本与 demo seed 两条直插 ready 产线在 put 后 HEAD 取 ETag 一并落列。
消费侧（Host 物化 If-Match / Worker claim 描述符）以本列判等检出「complete 后
同尺寸静默覆盖」。存量行 default '' 维持 base 的 size-only 语义（无身份可验）；
一次性残留窗口 = 升级前签出、升级时仍未过期的 PUT URL（≤1h）。存量行身份
建立（回填 + 门禁）拆至 #1316（#1312 第 11 轮收敛决策，方案 B）。

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
