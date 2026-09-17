"""Node-scoped artifact staging set for rerun/upgrade closures (#508/A3).

拆自 ``job_artifact_mutation``（文件预算；同一接缝）：``stage_outputs``
需要的「受影响闭包的暂存名集合」计算独立成纯函数——名字只在**闭包内
声明且闭包外无人共用**时才可安全移走本地文件（对抗审查 A3：继承节点
与重置节点同名 output 时，文件同时是继承节点的产物，移走会让 completed
节点 + 清单行指向空文件；共享名留给重跑原地覆盖，RMW 同款语义）。
"""

from __future__ import annotations

from server.app.workflows.definition import WorkflowDefinition


def staging_output_names(
    definition: WorkflowDefinition,
    affected_keys: set[str],
) -> set[str]:
    """Outputs staged for the affected closure; shared names excluded.

    A name declared as an output by any node **outside** the closure is
    never staged: the local file may be that outside node's artifact (the
    upgrade-inherit case: outside nodes are inherited, their artifacts must
    survive untouched), and deleting it would strand a completed node whose
    ``job_artifacts`` row then points at nothing. The rerunning node simply
    overwrites the file in place (RMW semantics). The returned set feeds
    both the file staging and the manifest-row deletion (same closure, same
    set — #508).
    """
    outside_outputs: set[str] = set()
    for key, node in definition.nodes.items():
        if key not in affected_keys:
            outside_outputs.update(set(node.outputs) - set(node.inputs))
    outputs: set[str] = set()
    for key in affected_keys:
        node = definition.nodes[key]
        outputs.update(set(node.outputs) - set(node.inputs))
    return outputs - outside_outputs
