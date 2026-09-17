"""Inherit 模式的继承集规划（issue #645）。

从 ``job_workflow_upgrade`` 的 diff 编排拆出：per-node diff（新旧定义
两侧 re-freeze 同基比较）+ 可达性退化，产出最终的 ``inherit_nodes`` 集
合。纯规划（读路径），事务外调用；失败语义只有「保守退化到更多重跑」。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_artifact_staging_scope import shared_name_rerun_closure
from server.app.services.job_workflow_upgrade_config import intake_frozen_config_json
from server.app.services.job_workflow_upgrade_diff import compute_inherit_reset_nodes
from server.app.services.job_workflow_upgrade_inherit import unreachable_inherit_nodes
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.workflow_branching import downstream_nodes


def plan_inherit_nodes(
    job_db: JobQueries,
    job: dict[str, Any],
    new_definition: WorkflowDefinition,
    new_frozen_config_json: str | None,
) -> frozenset[str]:
    """最终继承集 = 新定义可执行节点 − 变更子图 − 不可达子图（含下游闭包）。

    旧侧配置基准只用 job 的存量 ``frozen_config_json``（intake 冻结值，
    RUN-FREEZE-001）：产物是按那份冻结配置产出的，同基比较必须以它为
    旧侧输入。存量 NULL（legacy 作业或 config 面全空的作业）或快照解析
    失败都意味着**旧侧基准不可证明**：legacy 作业 dispatch 走现场解析，
    其产物基准是生产时刻的 workspace 配置，与升级时刻无关——在旧定义上
    按今天的配置 re-freeze 只会把配置演进吸收进旧侧（新旧同串恒等），
    让旧配置产物冒充新 revision 产物（对抗审查 A1）。旧定义按当前配置
    re-parse 抛 ``ValueError``（当前 override 对新 revision 有效但不满足
    旧快照的 config_schema，codex P2）同样不可证明，捕获后保守退化到
    全量重跑——与损坏快照 JSON 的降级方向一致：无法证明旧 config 等价
    → 保守退化为全量重跑，升级本身不因 legacy 配置漂移而失败。
    代价评估：NULL frozen 且新侧 re-freeze 非空（存在 config 面）的交集
    场景从「继承」变「全量」；两份 re-freeze 全空（无可冻结 config）时
    退化后哈希仍相等，继承面不受影响。

    产物不可达的节点除自身外，其在新图中的下游闭包一并移出继承集
    （review P1）：上游按新 revision 重跑后，其旧产物语义上已被替换，
    下游若继续继承旧输出，最终产物将基于已被丢弃的上游结果。下游
    闭包按新定义的边计算——新图里不再可达的节点本来就不在继承候选里。

    跨闭包同名输出（codex P1-3）：继承候选与重置面声明同名纯输出时，
    对象键 ``jobs/<ws>/<job>/<name>`` 不含 node 身份——重置节点重跑后
    上传按名字覆盖权威对象，继承节点的清单行从此指向别人的内容；暂存
    侧的同名排除（A3）在重置节点本次没真正写该文件时失效（
    ``_check_outputs`` 只查文件存在）。因此同名生产者一起重跑：候选中
    与重置面共享纯输出名的节点（及其下游闭包）移出继承集；升级事务内
    还会按实际保留集复算一次（``job_workflow_upgrade_cleanup``），覆盖
    未完成候选并入重置面的组合场景。
    """
    old_definition = definition_from_job_snapshot(job)
    if old_definition is None:
        # 快照解析失败（schema 演进/损坏）：无法证明旧侧任何等价性。
        return frozenset()
    old_frozen_config_json = job.get("frozen_config_json") or None
    if old_frozen_config_json is None:
        try:
            old_side_has_config = bool(
                intake_frozen_config_json(job_db, job["workspace_id"], old_definition)
            )
        except ValueError:
            # 旧快照按当前配置解析失败（codex P2）：旧侧基准不可证明，
            # 保守退化到 clean 语义（全量重跑）。
            return frozenset()
        if old_side_has_config:
            # 旧侧基准不可证明（A1）：保守退化到 clean 语义（全量重跑），
            # 不再走「旧定义 re-freeze 当前配置」的恒等回退。
            return frozenset()
    reset_nodes = compute_inherit_reset_nodes(
        old_definition,
        old_frozen_config_json,
        new_definition,
        new_frozen_config_json,
    )
    candidates = frozenset(new_definition.executable_nodes) - reset_nodes
    unreachable = unreachable_inherit_nodes(job_db, job, _jobs_dir(job_db), candidates)
    if unreachable:
        unreachable_closure: set[str] = set(unreachable)
        for node_key in unreachable:
            unreachable_closure.update(downstream_nodes(new_definition, node_key))
        candidates -= frozenset(unreachable_closure)
        # 不可达退化并入重置面：退化节点会重跑，共享其纯输出名的候选
        # 同样必须移出继承集（见下方 codex P1-3 排除）。
        reset_nodes |= unreachable_closure
    candidates -= shared_name_rerun_closure(new_definition, candidates, reset_nodes)
    return candidates


def _jobs_dir(job_db: JobQueries) -> Path:
    return job_db.jobs_dir
