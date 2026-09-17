"""Inherit 模式的继承集规划（issue #645）。

从 ``job_workflow_upgrade`` 的 diff 编排拆出：per-node diff（新旧定义
两侧 re-freeze 同基比较）+ 可达性退化，产出最终的 ``inherit_nodes`` 集
合。纯规划（读路径），事务外调用；失败语义只有「保守退化到更多重跑」。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_workflow_upgrade_config import intake_frozen_config_json
from server.app.services.job_workflow_upgrade_diff import compute_inherit_reset_nodes
from server.app.services.job_workflow_upgrade_inherit import unreachable_inherit_nodes
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.workflow_branching import downstream_nodes


def plan_inherit_nodes(
    job_db: JobQueries,
    job: dict[str, Any],
    workspace_id: str,
    new_definition: WorkflowDefinition,
    new_frozen_config_json: str | None,
) -> frozenset[str]:
    """最终继承集 = 新定义可执行节点 − 变更子图 − 不可达子图（含下游闭包）。

    旧侧配置基准优先用 job 的存量 ``frozen_config_json``（intake 冻结值，
    RUN-FREEZE-001）：产物是按那份冻结配置产出的，同基比较必须以它为
    旧侧输入。存量 NULL 只说明老 job 走 dispatch 现场解析（legacy 路径），
    这类遗留作业才回退到「在旧定义上按当前配置 re-freeze」——与新侧同一
    配置源，比较退化为纯定义 diff（现状基线）。旧定义按今天的 schema
    解析失败（schema 演进）时无法证明旧 config 等价，保守退化为全量重跑。

    产物不可达的节点除自身外，其在新图中的下游闭包一并移出继承集
    （review P1）：上游按新 revision 重跑后，其旧产物语义上已被替换，
    下游若继续继承旧输出，最终产物将基于已被丢弃的上游结果。下游
    闭包按新定义的边计算——新图里不再可达的节点本来就不在继承候选里。
    """
    old_definition = definition_from_job_snapshot(job) or new_definition
    old_frozen_config_json = job.get("frozen_config_json") or None
    if old_frozen_config_json is None:
        try:
            old_frozen_config_json = intake_frozen_config_json(job_db, workspace_id, old_definition)
        except ValueError:
            old_frozen_config_json = new_frozen_config_json
    reset_nodes = compute_inherit_reset_nodes(
        old_definition,
        old_frozen_config_json,
        new_definition,
        new_frozen_config_json,
    )
    candidates = frozenset(new_definition.executable_nodes) - reset_nodes
    unreachable = unreachable_inherit_nodes(job_db, job, _jobs_dir(job_db), candidates)
    if not unreachable:
        return candidates
    unreachable_closure: set[str] = set(unreachable)
    for node_key in unreachable:
        unreachable_closure.update(downstream_nodes(new_definition, node_key))
    return candidates - frozenset(unreachable_closure)


def _jobs_dir(job_db: JobQueries) -> Path:
    return job_db.jobs_dir
