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


def plan_inherit_nodes(
    job_db: JobQueries,
    job: dict[str, Any],
    workspace_id: str,
    new_definition: WorkflowDefinition,
    new_frozen_config_json: str | None,
) -> frozenset[str]:
    """最终继承集 = 新定义可执行节点 − 变更子图 − 产物不可达节点。

    旧侧基准用「在旧定义上 re-freeze」而非 job 的存量冻结值：存量
    NULL 只说明老 job 走 dispatch 现场解析（等价于当时的 re-freeze），
    拿 NULL 对比新 freeze 会把所有节点误判变更。两侧都 re-freeze 才是
    「执行输入是否变化」的同基比较；workspace 层的配置变化会同时体现
    在两侧 freeze 里。旧定义按今天的 schema 解析失败（schema 演进）时
    无法证明旧 config 等价，保守退化为全量重跑。
    """
    old_definition = definition_from_job_snapshot(job) or new_definition
    try:
        old_frozen = intake_frozen_config_json(job_db, workspace_id, old_definition)
    except ValueError:
        old_frozen = new_frozen_config_json
    reset_nodes = compute_inherit_reset_nodes(
        old_definition,
        old_frozen,
        new_definition,
        new_frozen_config_json,
    )
    candidates = frozenset(new_definition.executable_nodes) - reset_nodes
    unreachable = unreachable_inherit_nodes(job_db, job, _jobs_dir(job_db), candidates)
    return candidates - unreachable


def _jobs_dir(job_db: JobQueries) -> Path:
    return job_db.jobs_dir
