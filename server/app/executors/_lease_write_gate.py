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
    不存在并发 reset 还能改写的窗口。判活谓词与 ``finish_lease`` / broker
    清扫完全同源——lease 行存在、属于本 job、status='active'、落戳代次
    == 现值；ownership 的唯一撤销通道是 sweeper/expiry/finish 对 lease
    行的删除或状态翻转，它们同持 job-mutation 锁、与本复查互斥。

    不按 ``expires_at`` 单独判死（codex #774 P1）：心跳饥饿但控制面新鲜
    的 Worker 会被 HeartbeatDeferral 刻意保留 lease（静默 < 2×TTL 不清
    扫），``finish_lease`` 也只按 active 判活——写闸若额外按 expires_at
    关闸，会把仍被承认的结果的字节面判死，finish 再把成功节点永久翻成
    失败。清扫未介入的孤儿窗口（过期但行仍 active）里放行是良性竞态：
    此刻不存在竞争 attempt，sweep 提交后行即消失、闸自然关闭。
    """
    current = lock_job_mutation_and_read_generation(conn, job_id)
    if current is None:
        return False
    row = conn.execute(
        "select execution_generation from executor_leases"
        " where id=%s and job_id=%s and status='active'",
        (lease_id, job_id),
    ).fetchone()
    if row is None:
        return False
    return int(row["execution_generation"]) == current
