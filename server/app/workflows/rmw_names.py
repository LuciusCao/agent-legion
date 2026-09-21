"""RMW（read-modify-write，#114）产物名集合与升级保留集（#759 预算拆分）。

同名 input+output 的产物在重置时不暂存（删除会让节点死等自己产生的
输入）；凡本地文件被刻意保留的产物名，其权威副本（清单行 + 对象）必须
同保——保留文件 ⇔ 保留权威副本，否则 eviction/重启清走 job_dir 后
输入不可恢复。
"""

from __future__ import annotations

from server.app.workflows.definition import WorkflowDefinition


def rmw_artifact_names(*definitions: WorkflowDefinition | None) -> set[str]:
    """任一定义里同名 input+output 的产物名集合（#114 RMW）。"""
    names: set[str] = set()
    for definition in definitions:
        if definition is None:
            continue
        for node in definition.nodes.values():
            names.update(set(node.outputs) & set(node.inputs))
    return names


def upgrade_preserve_artifact_names(
    new_definition: WorkflowDefinition,
    old_definition: WorkflowDefinition | None,
) -> set[str]:
    """clean 升级中本地文件被刻意保留 ⇒ 清单行与对象必须同保的产物名。

    - 新定义 RMW 名（同名 input+output，#114）；
    - 共有节点的 output→input 转移名（旧 output、新纯 input）：暂存刻意
      保留文件（dropped_names 的 new_inputs 排除项），其清单行/对象也
      必须保留（#759 codex P1）。
    """
    names = rmw_artifact_names(new_definition)
    if old_definition is not None:
        for key in set(old_definition.executable_nodes) & set(new_definition.executable_nodes):
            names.update(
                set(old_definition.nodes[key].outputs) & set(new_definition.nodes[key].inputs)
            )
    return names
