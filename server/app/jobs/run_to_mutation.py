"""run-to（无起始节点）的原子 mutation（#759 预算拆分自 ``atomic_mutations``）。

EXEC-GENERATION-001：本路径的唯一 bump 点是 ``set_run_to_control`` 的
jobs UPDATE（run-to-with-start 同事务改由 ``mark_nodes_for_rerun`` bump）。
重置集（closure ∩ 非 completed，服务路径再经同名生产者收敛——codex
#776 复审 P1）在锁内确定，清单删除与分片行删除都由同一集合驱动
（重置集 ≡ 暂存集 ≡ 分片删除集）。闭包内的重置节点翻 pending 本轮
重跑；闭包外的翻 stale 失效（until_node 不可执行，下次 full run 重跑）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from server.app.agent_broker.manifest_trim import cancel_queued_sql
from server.app.db.connection import DatabaseConnection
from server.app.jobs.job_state_mutations import JobMutationConflict
from server.app.workflows.sharding import delete_shards

__all__ = ["apply_run_to", "set_run_to_control"]


def apply_run_to(
    conn: DatabaseConnection,
    job_id: str,
    target_node_key: str,
    closure: frozenset[str],
    *,
    reset_nodes: Sequence[str] | None = None,
    staged_artifact_names: frozenset[str] | set[str] = frozenset(),
) -> list[dict[str, Any]]:
    target = conn.execute(
        "select status from job_nodes where job_id=%s and node_key=%s",
        (job_id, target_node_key),
    ).fetchone()
    if target is None:
        raise ValueError(f"Unknown job node: {job_id}.{target_node_key}")
    if target["status"] == "completed":
        raise JobMutationConflict("target_already_completed", "Target node is already completed")

    if reset_nodes is None:
        # facade 路径（apply_run_to_atomic，仅测试在用）：reset 集在锁内现算
        # ——绝不能回落到全 closure（会把保持 completed 的分片节点行抹掉）。
        # 同名生产者收敛（codex #776 P1）只在服务路径做——直连 mutation 的
        # 调用面行为不变。
        current = {
            str(row["node_key"]): row["status"]
            for row in conn.execute(
                "select node_key, status from job_nodes where job_id=%s", (job_id,)
            )
        }
        reset_nodes = sorted(key for key in closure if current.get(key) != "completed")

    placeholders = ",".join("%s" for _ in closure)
    if not placeholders:
        raise ValueError("Run-to closure cannot be empty")
    # 正规化：调用方（或测试替身）给的任何可迭代都收敛成集合再判空，
    # 空集合必须跳过清单删除——空 join 会生成 name in () 语法错误。
    staged_artifact_names = frozenset(staged_artifact_names)
    deleted_rows: list[dict[str, Any]] = []
    if staged_artifact_names and reset_nodes:
        # #759：与 mark_nodes_for_rerun 同 invariant——被暂存产物（本地文件
        # 已移走）的清单行必须在同事务删除，否则 run-to 永不完成时作业仍在
        # 从对象存储提供上一轮产物。node 过滤用调用方算出的权威重置集
        # （closure ∩ 非 completed），与 stage_outputs 的暂存集同源。
        reset_marks = ",".join("%s" for _ in reset_nodes)
        name_marks = ",".join("%s" for _ in staged_artifact_names)
        deleted_rows = [
            dict(row)
            for row in conn.execute(
                f"""
                delete from job_artifacts
                where job_id=%s and node_key in ({reset_marks}) and name in ({name_marks})
                returning node_key, name, storage_key
                """,
                (job_id, *sorted(reset_nodes), *sorted(staged_artifact_names)),
            ).fetchall()
        ]
    # EXEC-GENERATION-001：run-to（无起始节点）路径的唯一 bump 点，fold 进
    # set_run_to_control 的 jobs UPDATE（run-to-with-start 在同事务里改走
    # mark_nodes_for_rerun 的 jobs UPDATE bump，这里不再 bump，整事务恰好一次）。
    generation = set_run_to_control(conn, job_id, target_node_key, bump_generation=True)
    # codex #776 复审 P1：reset_nodes 是调用方收敛后的权威重置集（含同名
    # 生产者）。闭包内的翻 pending 本轮重跑（until_node 允许集内，completed
    # 同名生产者也必须翻——不带 status != 'completed' 谓词，reset_nodes 已
    # 是锁内最终集合）；闭包外的翻 stale 失效（until_node 不可执行，下次
    # full run 重跑，与 with-start 臂对闭包外下游的既有语义一致）。
    pending_nodes = sorted(set(reset_nodes) & set(closure))
    if pending_nodes:
        pending_marks = ",".join("%s" for _ in pending_nodes)
        conn.execute(
            f"""
            update job_nodes
            set status='pending', stale_reason='', error_message='',
                started_at=null, finished_at=null, created_at=current_timestamp,
                execution_generation=%s
            where job_id=%s and node_key in ({pending_marks})
            """,
            (generation, job_id, *pending_nodes),
        )
    stale_nodes = sorted(set(reset_nodes) - set(closure))
    if stale_nodes:
        stale_marks = ",".join("%s" for _ in stale_nodes)
        conn.execute(
            f"""
            update job_nodes
            set status='stale', stale_reason='shared-name producer rerun',
                error_message='', created_at=current_timestamp,
                execution_generation=%s
            where job_id=%s and node_key in ({stale_marks})
            """,
            (generation, job_id, *stale_nodes),
        )
    # 已入队的 queued agent 请求不复查上游，重置节点前必须取消（见 mark_nodes_for_rerun）。
    cancel_scope = sorted(set(closure) | set(reset_nodes))
    cancel_marks = ",".join("%s" for _ in cancel_scope)
    conn.execute(cancel_queued_sql(cancel_marks), (job_id, *cancel_scope))
    # #759：分片行删除与节点重置同一集合——按全 closure 删会把保持
    # completed 的分片节点的 output_json 永久抹掉（reduce 重跑拼出空输入）。
    delete_shards(conn, job_id, reset_nodes)
    return deleted_rows


def set_run_to_control(
    conn: DatabaseConnection,
    job_id: str,
    target_node_key: str,
    *,
    bump_generation: bool = False,
) -> int | None:
    """Write the run-to execution control row; optionally bump the epoch.

    ``bump_generation=True``（仅 ``apply_run_to``）把 EXEC-GENERATION-001 的
    代次 +1 fold 进同一条 jobs UPDATE 并返回新代次，供调用方给重置的
    job_nodes 行盖戳；默认 False 供 run-to-with-start 使用——同事务的
    bump 已由 mark_nodes_for_rerun 承担，这里再 bump 就是双重 +1。
    """
    if not bump_generation:
        conn.execute(
            """
            update jobs
            set status='queued', execution_mode='until_node', target_node_key=%s,
                execution_paused=0, pause_reason='', error_message='',
                updated_at=current_timestamp
            where id=%s
            """,
            (target_node_key, job_id),
        )
        return None
    row = conn.execute(
        """
        update jobs
        set status='queued', execution_mode='until_node', target_node_key=%s,
            execution_paused=0, pause_reason='', error_message='',
            execution_generation=execution_generation+1,
            updated_at=current_timestamp
        where id=%s
        returning execution_generation
        """,
        (target_node_key, job_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"Job not found: {job_id}")
    return int(row["execution_generation"])
