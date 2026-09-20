"""EXEC-GENERATION-001 产物字节写闸（#645 P2）。

代次协议原先只护 DB 状态面（job_nodes/jobs 的 finish CAS）；产物字节面有
两个写口在 finish CAS 之前/之外落地——Worker 回传的 promote（authority
copy + job_dir 落盘 + 清单行登记）与本地 code 孤儿执行的 D12 镜像上传。
本模块把与 mutation 侧（``lease_guarded_mutation``）互斥的复查提供给这些
写路径：``job-mutation:<job_id>`` advisory 锁下读 jobs 现值代次，并复查
lease 行属于本 job、仍 active、落戳代次与现值一致。复查不过 = 本次产物
整体丢弃（不 copy、不上传、不写盘、不登记清单），由调用方按既有的
409/失败路径收尾。
"""

from __future__ import annotations

from server.app.db.connection import DatabaseConnection
from server.app.executors._lease_control import lock_job_mutation_and_read_generation


def lease_artifact_write_current(conn: DatabaseConnection, lease_id: str, job_id: str) -> bool:
    """True = 该 lease 仍持有本 job 当前代次的产物写权；调用方须在写事务内。

    锁序与协议一致：job-mutation advisory 锁先于一切行读；锁下读到的代次
    不存在并发 reset 还能改写的窗口。lease 行不存在（agent sweep 删行）、
    已 released/expired（本地孤儿、已 finish）、心跳已过期（sweeper 介入
    前的孤儿窗口）或代次不符（reset 已 bump）一律 False。
    """
    current = lock_job_mutation_and_read_generation(conn, job_id)
    if current is None:
        return False
    row = conn.execute(
        "select execution_generation from executor_leases"
        " where id=%s and job_id=%s and status='active' and expires_at > current_timestamp",
        (lease_id, job_id),
    ).fetchone()
    if row is None:
        return False
    return int(row["execution_generation"]) == current
