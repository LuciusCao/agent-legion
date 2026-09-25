"""Inherit 模式的继承集规划（issue #645）。

从 ``job_workflow_upgrade`` 的 diff 编排拆出：per-node diff（新旧定义
两侧 re-freeze 同基比较）+ 可达性退化，产出最终的 ``inherit_nodes`` 集
合。纯规划（读路径），事务外调用；失败语义只有「保守退化到更多重跑」。

702 传播闭包重构：``plan_inherit_nodes`` 总装种子集 + 闭包——S1–S5 局部
种子经 ``collect_change_seeds``（diff 比较器双侧编排），S6 可达性种子
对「未被 S1–S5 命中的候选」探测后并入种子再闭包（保持既有短路：闭包
内节点必然重跑，无需探测可达性）。``rerun_closure`` 是唯一的重置面来
源（通道 A 显式边传播 + 通道 B 同名生产者 fixpoint + 通道 C 隐式消费
边，#759）——上游一致性由「全部上游都不在重跑闭包里」直接定义。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_workflow_upgrade_config import intake_frozen_config_json
from server.app.services.job_workflow_upgrade_impl import implementation_excluded_nodes
from server.app.services.job_workflow_upgrade_inherit import unreachable_inherit_nodes
from server.app.services.job_workflow_upgrade_propagation import (
    collect_change_seeds,
    rerun_closure,
)
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.workflows.definition import WorkflowDefinition


def plan_inherit_nodes(
    job_db: JobQueries,
    job: dict[str, Any],
    new_definition: WorkflowDefinition,
    new_frozen_config_json: str | None,
    *,
    custom_nodes_enabled: bool = True,
    require_manifest_rows: bool = False,
) -> frozenset[str]:
    """最终继承集 = 新定义可执行节点 −（S1–S5 种子 ∪ S6 可达性种子）的传播闭包。

    ``custom_nodes_enabled``（P1-1）与 dispatch 侧同一特性 gate
    （``workflows.custom_nodes_enabled``）：关闭时 code 节点当前身份
    不可解析，全部保守重跑（与「关闭特性时 dispatch 无 code 可跑」的
    现实一致）。skill 内容身份（codex 五轮 P1-A，#759 收紧）由
    ``implementation_excluded_nodes`` 直读 DB 锁文档判定（latest 恒定
    排除、pinned 无锁条目即不可证明、upgrade 永不 pin/不跑 git），与
    guard 事务内重验走同一权威读取。

    ``require_manifest_rows``（codex #776 复审 P2）：对象存储权威层启用
    时 S6 可达性要求每个声明 output 都有 ``job_artifacts`` 清单行——
    本地文件是可淘汰缓存（EXEC-ARTIFACT-STORE-001），仅有本地文件的
    completed 节点退化重跑（重跑会重新上传，自愈缺失的权威副本）。

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

    产物不可达的节点（S6）作为种子并入闭包（review P1）：上游按新
    revision 重跑后其旧产物语义上已被替换，下游若继续继承旧输出，最终
    产物将基于已被丢弃的上游结果——闭包传播天然覆盖下游。S6 只对未被
    S1–S5 命中的候选探测（闭包内节点必然重跑，无需探测）。

    跨闭包同名输出（codex P1-3）：继承候选与重置面声明同名输出（含
    RMW）时，
    对象键 ``jobs/<ws>/<job>/<name>`` 不含 node 身份——重置节点重跑后
    上传按名字覆盖权威对象，继承节点的清单行从此指向别人的内容；暂存
    侧的同名排除（A3）在重置节点本次没真正写该文件时失效（
    ``_check_outputs`` 只查文件存在）。因此同名生产者一起重跑（通道 B，
    ``rerun_closure`` 内的 ``shared_name_rerun_closure`` fixpoint）；升级
    事务内还会按实际保留集复算一次（``job_workflow_upgrade_staging``），
    覆盖未完成候选并入重置面的组合场景。

    实现身份（codex 四轮 P1-1）：实现重发布而节点定义未变时，定义
    哈希两侧相等——旧产物按旧实现产出、升级后同节点重跑执行新实现。
    ``job_workflow_upgrade_impl`` 比较该节点最新完成执行的记录身份
    （v84 起 ``node_runs.agent_definition_hash`` 优先、请求行 fallback）
    与当前 published 身份：证明相等才可继承；漂移或不可证明（记录被
    retention 清扫、实现未发布）→ 该节点进 S4 种子，经闭包传播到下游。
    复审 HIGH-2：Agent 定义 schema 含 runtime_mutable 键的 agent 节点
    同在排除集——定义不变、只翻转 override 值再翻回时 frozen/实现身份
    两侧全等，diff 层节点自声明判定覆盖不到定义侧键（详见 impl 模块）。
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
    implementation_excluded = implementation_excluded_nodes(
        job_db,
        job,
        new_definition,
        custom_nodes_enabled=custom_nodes_enabled,
    )
    seeds = collect_change_seeds(
        old_definition,
        old_frozen_config_json,
        new_definition,
        new_frozen_config_json,
        implementation_excluded,
    )
    reset_nodes = rerun_closure(new_definition, seeds)
    candidates = frozenset(new_definition.executable_nodes) - reset_nodes
    unreachable = unreachable_inherit_nodes(
        job_db,
        job,
        _jobs_dir(job_db),
        candidates,
        require_manifest_rows=require_manifest_rows,
    )
    if unreachable:
        # S6 可达性种子并入再闭包：不可达候选的下游沿闭包传播重跑，
        # 共享其输出名（含 RMW）的候选经通道 B 一并移出继承集。
        reset_nodes = rerun_closure(new_definition, seeds | set(unreachable))
        candidates = frozenset(new_definition.executable_nodes) - reset_nodes
    return candidates


def _jobs_dir(job_db: JobQueries) -> Path:
    return job_db.jobs_dir
