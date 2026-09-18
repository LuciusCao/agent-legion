"""Node-scoped artifact staging set for rerun/upgrade closures (#508/A3).

拆自 ``job_artifact_mutation``（文件预算；同一接缝）：``stage_outputs``
需要的「受影响闭包的暂存名集合」计算独立成纯函数——名字只在**闭包内
声明且闭包外无人共用**时才可安全移走本地文件（对抗审查 A3：继承节点
与重置节点同名 output 时，文件同时是继承节点的产物，移走会让 completed
节点 + 清单行指向空文件）。

codex P1-3：同名排除对 **rerun/run-to 闭包**仍是正确语义（共享名留给
重跑原地覆盖，RMW 同款）；但 upgrade-inherit 不能依赖它——对象键按
``jobs/<ws>/<job>/<name>`` 不含 node 身份，重置节点重跑后按名字覆盖
权威对象，继承节点的清单行会指向别人的内容；且重置节点本次没真正写
该文件时，``_check_outputs`` 只查文件存在，会把继承节点的旧字节当本
次输出重新上传。继承侧改为「同名生产者一起重跑」：见
``shared_name_rerun_closure``（plan 阶段确定性排除 + 升级事务内按实际
保留集收敛），A3 排除在此只是文件系统安全的兜底而非常正确性依赖。
"""

from __future__ import annotations

from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.workflow_branching import downstream_nodes


def staging_output_names(
    definition: WorkflowDefinition,
    affected_keys: set[str],
) -> set[str]:
    """Outputs staged for the affected closure; shared names excluded.

    A name declared as an output by any node **outside** the closure is
    never staged: the local file may be that outside node's artifact, and
    deleting it would strand a completed node whose ``job_artifacts`` row
    then points at nothing. For rerun/run-to closures the rerunning node
    simply overwrites the file in place (RMW semantics). For upgrade-inherit
    this exclusion alone is NOT a correctness mechanism (see the module
    docstring, codex P1-3): same-name producers are rerun together via
    ``shared_name_rerun_closure``, so by the time this runs the whole
    producer set of a shared name is inside the closure. The returned set
    feeds both the file staging and the manifest-row deletion (#508).
    """
    outside_outputs: set[str] = set()
    for key, node in definition.nodes.items():
        if key not in affected_keys:
            # RMW names are still outputs owned by the outside node.  An
            # affected pure producer with the same name must not strand it.
            outside_outputs.update(node.outputs)
    affected_nodes = [definition.nodes[key] for key in affected_keys]
    outputs = {name for node in affected_nodes for name in set(node.outputs) - set(node.inputs)}
    affected_rmw = {
        name for node in affected_nodes for name in set(node.outputs) & set(node.inputs)
    }
    # RMW is name-scoped here: any affected node that needs the current value
    # as startup input protects the shared path from staging.
    return outputs - outside_outputs - affected_rmw


def _producer_outputs(definition: WorkflowDefinition, key: str) -> set[str]:
    return set(definition.nodes[key].outputs)


def shared_name_rerun_closure(
    definition: WorkflowDefinition,
    keep: frozenset[str],
    reset_face: set[str],
) -> set[str]:
    """Keep-set nodes that must rerun because they share an output name
    with the reset face, plus their downstream closure (codex P1-3).

    Same-name producers cannot be split across the inherit/reset boundary:
    whichever writes last owns the shared object key, and the loser's
    manifest row points at foreign content. Returns the subset of ``keep``
    to move into the reset face, computed to a fixpoint — each exclusion
    joins the face and may cascade through further name sharing or
    downstream edges (an excluded node reruns, so its old outputs are
    semantically replaced and its kept descendants cannot inherit them).
    Conservative direction: extra reruns, never crossed data.
    """
    excluded: set[str] = set()
    while True:
        face = reset_face | excluded
        face_names: set[str] = set()
        for key in face:
            face_names.update(_producer_outputs(definition, key))
        if not face_names:
            return excluded
        newly = {key for key in keep - excluded if _producer_outputs(definition, key) & face_names}
        if not newly:
            return excluded
        excluded |= newly
        for node_key in newly:
            excluded.update(set(downstream_nodes(definition, node_key)) & (set(keep) - excluded))
