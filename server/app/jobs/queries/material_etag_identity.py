"""#1279：材料 object_etag 身份的重绑查询面（queries 层）。

两个模块级函数（ConnectSource 驱动，facade 薄壳在 material_repair.py
delegate；BOUNDARY-DATA-001：service 与脚本侧经 ConnectSource 直通，
不手写 SQL）：

- ``bind_material_etag_if_current``——CAS 落 object_etag：仅当行的
  （object_etag, storage_key, upload_generation, status）四钉仍等于扫描
  快照时落新值（#1313）。新 etag 是从**扫描时的 key** 核验出来的，四钉
  缺一都可能把它绑到别的身份上：repair 清 etag、轮换 key、代次 +1，重传
  同字节后 etag 可 ABA 回到期望值（key/代次钉拦）；presign 重试轮换 key
  与代次却**不清 etag**（etag 钉拦不住，key/代次钉拦）；TTL 过期与
  complete 失败只翻 status（只有 status 钉拦）。写者 × 字段逐格测试见
  tests/services/test_material_rebind_races.py。消费方：恢复重绑脚本。
- ``materials_with_object_etag``——恢复重绑扫描面（运维低频全量扫描，
  无索引可接受）；带出四钉的扫描快照。

存量 ``object_etag=''`` 行的身份建立（回填）不在本 PR：拆至 #1316，
这些行维持 base 的 size-only 语义（见 materials-and-runs-design §6.4.1）。
"""

from __future__ import annotations

from typing import Any, TypedDict

from server.app.db.dialect import ConnectSource
from server.app.db.transaction import read_connection, write_transaction


class EtagRebindCas(TypedDict):
    """重绑 CAS 的关键字参数：新 etag + 扫描快照四钉（facade 以 Unpack 透传）。"""

    expected_etag: str
    new_etag: str
    storage_key: str
    upload_generation: int
    status: str


def bind_material_etag_if_current(
    connect_source: ConnectSource,
    material_id: str,
    *,
    expected_etag: str,
    new_etag: str,
    storage_key: str,
    upload_generation: int,
    status: str,
) -> bool:
    """四钉 CAS 落 object_etag（见模块 docstring）；返回是否落库。
    False = 扫描后行被并发改写或删除，调用方按竞态失败报告。"""
    with write_transaction(connect_source) as conn:
        result = conn.execute(
            "update materials set object_etag=%s where id=%s and object_etag=%s"
            " and storage_key=%s and upload_generation=%s and status=%s",
            (new_etag, material_id, expected_etag, storage_key, upload_generation, status),
        )
    return result.rowcount > 0


def materials_with_object_etag(connect_source: ConnectSource) -> list[dict[str, Any]]:
    """恢复重绑扫描面：记录了 object_etag 的全部材料行（跨 workspace）。"""
    with read_connection(connect_source) as conn:
        rows = conn.execute(
            "select id, workspace_id, content_hash, size_bytes, storage_key,"
            " object_etag, upload_generation, status from materials where object_etag <> ''"
        ).fetchall()
    return [dict(row) for row in rows]
