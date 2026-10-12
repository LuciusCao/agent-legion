"""materials 行锁内复查（#1267 codex R2/R3 P1；与 ``job_locked_rereads`` 同型）。

complete() 的核验在事务外做对象 I/O；落 ready/failed 前必须在行锁内重读
核验身份，防并发 presign 重试把行改嫁到未核验的字节——锁外快照到落库之间
的窗口由本模块的锁内当前状态判定关闭。身份 = storage_key + size_bytes +
upload_generation：代次（schema v96，每次 presign 重试 +1）让同 key 同
大小的 uploading→uploading 重试也可见（R3 P1）；重试同时把行迁到
``g{代次}`` 段的新 key（R4 P1），此前签发的 PUT URL 全部指向死 key。
"""

from typing import Any

from server.app.services.job_errors import ConflictError, NotFoundError


def assert_material_upload_identity_tx(conn: Any, material_id: str, verified_row: dict) -> str:
    """FOR UPDATE 锁定材料行，比对 ``verified_row``（该次 complete 实际核验的
    快照）的 storage_key / size_bytes / upload_generation，返回锁内当前状态。

    - 行已被并发删除 → NotFoundError（delete 持同一行锁，行消失只可能是它
      已提交）；
    - 身份在核验期间被 presign 重试改写（含代次推进）→ ConflictError，客户
      端重调 complete 即按新身份重新核验；
    - 身份一致时由调用方按返回的状态决定落子：``ready`` 表示并发 complete
      已按同一身份完成（幂等，不许用本方结论覆盖）；``expired`` 表示核验
      期间被 TTL 翻走（调用方按 expired 冲突处理）。
    """
    row = conn.execute(
        "select storage_key, size_bytes, status, upload_generation"
        " from materials where id=%s for update",
        (material_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Material not found: {material_id}")
    if (
        str(row["storage_key"]) != str(verified_row["storage_key"])
        or int(row["size_bytes"]) != int(verified_row["size_bytes"])
        or int(row["upload_generation"]) != int(verified_row["upload_generation"])
    ):
        raise ConflictError(
            f"Material {material_id} changed during verification; call complete again"
        )
    return str(row["status"])
