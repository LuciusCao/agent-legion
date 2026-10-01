"""Node-scoped artifact staging set for rerun/upgrade closures (#508/A3).

拆自 ``job_artifact_mutation``（文件预算；同一接缝）：``stage_outputs``
需要的「受影响闭包的暂存名集合」计算独立成纯函数——名字只在**闭包内
声明且闭包外无人共用**时才可安全移走本地文件（对抗审查 A3：继承节点
与重置节点同名 output 时，文件同时是继承节点的产物，移走会让 completed
节点 + 清单行指向空文件）。

同名纯输出不能跨重置边界拆分（codex #776 复审 P1，与 upgrade 通道 B
同语义）：重置闭包的同名生产者收敛见 ``job_reset_closure``（rerun /
rework / run-to / upgrade 共用），A3 排除在此只是文件系统安全的兜底
而非正确性依赖。
"""

from __future__ import annotations

from server.app.workflows.definition import WorkflowDefinition


def staging_output_names(
    definition: WorkflowDefinition,
    affected_keys: set[str],
) -> set[str]:
    """Outputs staged for the affected closure; shared names excluded.

    A name declared as an output by any node **outside** the closure is
    never staged: the local file may be that outside node's artifact, and
    deleting it would strand a completed node whose ``job_artifacts`` row
    then points at nothing. Callers compute the closure via
    ``job_reset_closure`` (rerun/rework/run-to) or the upgrade keep/reset
    convergence, both of which pull same-name producers INTO the reset face
    first (codex #776 P1 / upgrade 通道 B) — so by the time this runs the
    whole producer set of a shared name is inside the closure and this
    exclusion is only a filesystem-safety backstop. The returned set feeds
    both the file staging and the manifest-row deletion (#508).
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
