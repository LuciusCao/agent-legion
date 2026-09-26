"""run-to 的两个执行臂（#759 预算拆分自 ``job_execution``）。

与 ``job_rerun/single.py`` 同模式：模块级函数以 service 为首参，服务层只留
路由与校验。两条臂共享同一个 invariant——重置集 ≡ 暂存集（stage_outputs
不做任何图遍历，权威集合由本模块计算）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from server.app.events.aggregator import broadcast_job_update, record_job_update
from server.app.jobs.atomic_mutations import JobMutationConflict
from server.app.jobs.run_to_mutation import apply_run_to
from server.app.services.job_operation_error import JobOperationError, JobOperationResult
from server.app.services.job_rerun.upstream_guard import (
    raise_if_failed_upstream,
    raise_if_failed_upstream_in_tx,
)
from server.app.services.job_reset_closure import rerun_reset_closure, run_to_reset_nodes
from server.app.services.job_staged_cleanup import (
    commit_staged_outputs,
    delete_rerun_artifact_objects,
)
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.start_node import START_NODE_TYPE

if TYPE_CHECKING:
    from server.app.services.job_execution import JobExecutionService

logger = logging.getLogger(__name__)


def run_to_without_start(
    service: JobExecutionService,
    job: dict[str, Any],
    definition: WorkflowDefinition,
    target_node_key: str,
    closure: frozenset[str],
) -> JobOperationResult:
    job_id = str(job["id"])
    node_statuses = {
        node["node_key"]: node["status"] for node in service.job_db.list_job_nodes(job_id)
    }

    if node_statuses.get(target_node_key) == "completed":
        raise JobOperationError(
            job_id,
            "run_to",
            "skipped",
            target_node_key,
            "target_already_completed",
            "Target node is already completed",
        )

    # #759：重置集 = closure ∩ 非 completed，且必须在 mutation 锁内重读
    # （TOCTOU：锁外读数到取锁之间节点可能被 claim 并完成——用过期集合
    # 暂存会清掉已完成节点的权威产物，而节点 UPDATE 的 status 谓词又把它
    # 留在 completed，永久失去产物且不重跑）。锁内读数是最终状态：所有
    # 状态写入方都持同一把 job-mutation 锁。暂存/清单删除/节点重置由这
    # 同一个当前集合驱动，三者不可能再分叉。
    staged = None
    deleted_rows: list[dict[str, Any]] = []
    try:
        with service.job_db.lease_guarded_mutation(
            job_id,
            service._now(),
            reject_running_nodes=True,
        ) as conn:
            current_statuses = service.job_db.list_job_node_statuses_in_transaction(conn, job_id)
            # codex #776 复审 P1：同名纯输出生产者一并进重置面（与 rerun /
            # with-start 同语义）。重置集 ≡ 暂存集：收敛后的同一集合同喂
            # stage_outputs 与 apply_run_to。
            reset_nodes = run_to_reset_nodes(definition, closure, current_statuses)
            # 本臂刻意不做 failed-upstream 守卫（与 with-start 不对称是
            # 设计）：failed 祖先必落在 closure ∩ 非 completed 的重置集里，
            # 一并翻 pending 重跑，无「遗留 failed 祖先 → 永远 queued」
            # 的隐患（test_job_run_to_upstream_guard 模块 docstring 钉住）。
            staged = service.artifact_mutation.stage_outputs(job, reset_nodes, definition)
            deleted_rows = apply_run_to(
                conn,
                job_id,
                target_node_key,
                closure,
                reset_nodes=reset_nodes,
                staged_artifact_names=staged.artifact_names,
            )
    except JobMutationConflict as exc:
        if staged is not None:
            staged.rollback()
        raise JobOperationError(
            job_id,
            "run_to",
            "skipped",
            target_node_key,
            exc.reason_code,
            str(exc),
        ) from exc
    except ValueError as exc:
        if staged is not None:
            staged.rollback()
        raise JobOperationError(
            job_id, "run_to", "failed", target_node_key, "node_not_found", str(exc)
        ) from exc
    except Exception:
        # #204 broad-except audit: terminal safety net of the staged
        # run-to mutation, mirroring the with-start arm below. Conflict
        # (→ skipped) and contract (ValueError → failed) are handled
        # above; this arm guarantees the staged artifacts are rolled
        # back before an unexpected error escapes — otherwise outputs
        # vanish from the job dir while the DB still marks them present.
        if staged is not None:
            staged.rollback()
        raise
    commit_staged_outputs(staged, job_id, "run_to")
    delete_rerun_artifact_objects(service.object_store, deleted_rows, job_id, "run_to")

    if service.job_event_buffer is not None:
        record_job_update(
            service.job_db, service.job_event_buffer, job_id, str(job["workspace_id"])
        )
    elif service.job_event_manager is not None:
        broadcast_job_update(service.job_db, service.job_event_manager, job_id)
    return service._result(job_id, "run_to", "succeeded", target_node_key)


def run_to_with_start(
    service: JobExecutionService,
    job: dict[str, Any],
    definition: WorkflowDefinition,
    target_node_key: str,
    start_node_key: str,
    closure: frozenset[str],
) -> JobOperationResult:
    job_id = str(job["id"])
    if start_node_key not in definition.nodes:
        raise JobOperationError(
            job_id,
            "run_to",
            "failed",
            target_node_key,
            "node_not_found",
            f"Start node {start_node_key} not found in workflow",
        )
    if definition.nodes[start_node_key].node_type == START_NODE_TYPE:
        raise JobOperationError(
            job_id,
            "run_to",
            "failed",
            target_node_key,
            "node_not_executable",
            f"Node {start_node_key} is an entry (type: start) node and never executes",
        )

    if start_node_key not in closure:
        raise JobOperationError(
            job_id,
            "run_to",
            "failed",
            target_node_key,
            "invalid_start",
            f"Start node {start_node_key} is not in the target closure",
        )

    # Same hazard as rerun: only the start node and its downstream are
    # reset, so a failed ancestor would strand the job in queued forever.
    raise_if_failed_upstream(
        definition,
        service.job_db.list_job_nodes(job_id),
        start_node_key,
        job_id,
        "run_to",
        target_node_key,
    )

    staged = None
    deleted_rows: list[dict[str, Any]] = []
    try:
        # #759：暂存集合与重置集合同源——closure 只界定 run-to 的执行
        # 范围，不参与暂存判定；目标闭包外的隐式下游同样在重置集里，
        # 其旧产物必须一并失效（stage_outputs 不做任何图遍历）。
        # codex #776 复审 P1：同名纯输出生产者一并进重置面
        # （rerun_reset_closure 统一收敛，与 rerun 同语义）。
        affected = sorted(rerun_reset_closure(definition, [start_node_key]))
        descendants = [key for key in affected if key != start_node_key]
        with service.job_db.lease_guarded_mutation(
            job_id,
            service._now(),
            reject_running_nodes=True,
        ) as conn:
            # #759 invariant 5：failed-upstream 资格在锁内用当前状态重查。
            raise_if_failed_upstream_in_tx(
                service.job_db,
                conn,
                definition,
                start_node_key,
                job_id,
                "run_to",
                target_node_key,
                stale_nodes=descendants,
            )
            staged = service.artifact_mutation.stage_outputs(job, affected, definition)
            deleted_rows = service.job_db.mark_nodes_for_rerun_in_transaction(
                conn,
                job_id,
                [start_node_key],
                {start_node_key: descendants},
                staged_artifact_names=staged.artifact_names,
            )
            service.job_db.set_run_to_control_in_transaction(conn, job_id, target_node_key)
    except JobMutationConflict as exc:
        if staged is not None:
            staged.rollback()
        raise JobOperationError(
            job_id, "run_to", "skipped", target_node_key, exc.reason_code, str(exc)
        ) from exc
    except JobOperationError:
        # 锁内 failed-upstream 重查的业务拒绝：回滚暂存后原样抛出。
        if staged is not None:
            staged.rollback()
        raise
    except ValueError as exc:
        if staged is not None:
            staged.rollback()
        raise JobOperationError(
            job_id, "run_to", "failed", target_node_key, "cleanup_failed", str(exc)
        ) from exc
    except Exception as exc:
        # #204 broad-except audit: the terminal safety net of a
        # staged filesystem + DB mutation sequence. The business arms
        # above already peeled off the concurrency conflict
        # (JobMutationConflict → skipped) and the staged-output
        # contract violations (ValueError → cleanup_failed); what lands
        # here is the genuinely unexpected (DB connectivity mid-mutation,
        # a bug). Either way the staged files must be rolled back before
        # normalizing to JobOperationError — leaving them staged would
        # strand artifacts the rerun just removed from their original
        # locations. logger.exception keeps the traceback.
        logger.exception("Failed to persist run-to target for job %s", job_id)
        if staged is not None:
            staged.rollback()
        raise JobOperationError(
            job_id,
            "run_to",
            "failed",
            target_node_key,
            "rerun_failed",
            str(exc),
        ) from exc

    commit_staged_outputs(staged, job_id, "run-to")
    delete_rerun_artifact_objects(service.object_store, deleted_rows, job_id, "run-to")
    if service.job_event_buffer is not None:
        record_job_update(
            service.job_db, service.job_event_buffer, job_id, str(job["workspace_id"])
        )
    elif service.job_event_manager is not None:
        broadcast_job_update(service.job_db, service.job_event_manager, job_id)
    return service._result(job_id, "run_to", "succeeded", target_node_key)
