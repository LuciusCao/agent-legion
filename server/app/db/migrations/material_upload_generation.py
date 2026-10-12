"""Schema v97: ``materials.upload_generation`` — 上传代次（#1267 codex R3 P1）。

complete() 落 ready/failed 前的锁内复查（jobs/queries/material_locked_rereads.py）
按 verify 的输入身份比对；其中「同 hash 同 key 同大小的重试」会把行重置为
uploading 并签发新 PUT URL，而 key/size 都不变，对纯内容身份不可见——本列是
每次 presign 重试都 +1 的代次计数器，让 uploading→uploading 的重试在锁内
复查中可见。default 0 保持存量行一致（legacy 行的首次重试落到 1）。

Born as v95 on its branch; renumbered twice per the #434 collision protocol
(the later merge renumbers): v95→v96 when main took v95 for
``studio_chat_message_draft_kind``, then v96→v97 when #1226 took v96 for
``workflow_draft_generation``. Same guarded-ALTER home rule as v87+: the
column lives ONLY here (postgres_schema.sql sits at its budget ceiling),
idempotent on replay, and both install paths run the chain anyway.
"""

from __future__ import annotations

from typing import Any

_UPLOAD_GENERATION_DDL = """
alter table materials
  add column if not exists upload_generation integer not null default 0;
"""


def migrate_material_upload_generation(conn: Any) -> None:
    """Add the upload-generation column (v97, #1267); idempotent."""
    conn.execute(_UPLOAD_GENERATION_DDL)
