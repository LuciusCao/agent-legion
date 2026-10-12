"""materials 行锁内复查（#1267 codex R2 P1；与 ``job_locked_rereads`` 同型）。

complete() 的核验在事务外做对象 I/O；落 ready/failed 前必须在行锁内重读
核验身份，防并发 presign 重试（重建收紧前的非法 legacy key、改声明大小）
把行改嫁到未核验的字节——锁外快照到落库之间的窗口由本模块的锁内当前状态
判定关闭。
"""

from typing import Any

from server.app.services.job_errors import ConflictError, NotFoundError


def assert_material_upload_identity_tx(conn: Any, material_id: str, verified_row: dict) -> None:
    """FOR UPDATE 锁定材料行，比对 ``verified_row``（该次 complete 实际核验的
    快照）的 storage_key 与 size_bytes。

    行已被并发删除 → NotFoundError（delete 持同一行锁，行消失只可能是它
    已提交）；身份在核验期间被改写 → ConflictError，客户端重调 complete
    即按新身份重新核验。
    """
    row = conn.execute(
        "select storage_key, size_bytes from materials where id=%s for update",
        (material_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Material not found: {material_id}")
    if str(row["storage_key"]) != str(verified_row["storage_key"]) or int(row["size_bytes"]) != int(
        verified_row["size_bytes"]
    ):
        raise ConflictError(
            f"Material {material_id} changed during verification; call complete again"
        )
