import os
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from server.app.services.job_artifact_names import (
    NON_ARTIFACT_DIR_NAMES,
    is_downloadable_artifact_name,
)
from server.app.services.job_node_ordering import effective_after, ordered_job_nodes
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.revision_format import definition_from_job_snapshot

if TYPE_CHECKING:
    from server.app.settings import Settings


def job_nodes_with_definition(nodes: list[dict], definition: WorkflowDefinition) -> list[dict]:
    def _field(node: dict, attr: str, default: object) -> object:
        # 声明侧缺该节点（definition 演进后）时回落默认值。
        target = definition.nodes.get(str(node["node_key"]))
        return getattr(target, attr) if target is not None else default

    return [
        {
            **node,
            "label": _field(node, "label", node["node_key"]),
            "capability": _field(node, "capability", node["node_key"]),
            "after": effective_after(definition, node["node_key"]),
            "inputs": _field(node, "inputs", []),
            "outputs": _field(node, "outputs", []),
        }
        for node in ordered_job_nodes(nodes, definition)
    ]


def node_summary(node: dict, definition: WorkflowDefinition) -> dict:
    node_key = str(node["node_key"])
    label = definition.nodes[node_key].label if node_key in definition.nodes else node_key
    return {
        "node_key": node_key,
        "label": label,
        "status": str(node["status"]),
        "error_message": str(node.get("error_message", "")),
    }


def artifact_names(job: dict, settings: "Settings") -> list[str]:
    base = resolve_job_dir(job, settings.jobs_dir)
    if not base.exists():
        return []
    return sorted(path.name for path in base.iterdir() if path.is_file())


# 剪枝名单与下载侧白名单的单一事实来源在 job_artifact_names（M2）。
_PRUNED_DIRS = NON_ARTIFACT_DIR_NAMES


def _declared_output_names(job: dict) -> frozenset[str]:
    """Job 快照声明 outputs；无/坏快照（legacy 行）解析为 None → 空集。"""
    definition = definition_from_job_snapshot(job)
    if definition is None:
        return frozenset()
    return frozenset(name for node in definition.nodes.values() for name in node.outputs)


def artifact_names_deep(
    job: dict, settings: "Settings", declared: frozenset[str] | None = None
) -> list[str]:
    """Recursive local-artifact scan for job-dir-relative subpath names
    (#631 review P2-1)：声明产物可落子目录（reports/final.json），根级扫
    描看不见。剪枝 ``runs/``/点前缀子树；符号链接不跟随；名字过下载侧
    白名单（#631 codex3，list→download 对称）。

    声明门（#703 codex round 4 P2-1）：名字必须 ∈ job 快照声明 outputs
    （``declared``，None 时此处解析快照）——code 节点留在 job_dir 的未声
    明嵌套文件（scratch/debug.json）不再被列为 local 产物、不再被轮询遍
    历（job_dir 是执行暂存面，产物面由声明与 manifest 行定义；行不受本
    函数约束，行是执行产物的登记面）。无快照的 legacy job 本地清单为空
    （下载门同语义，列举与下载对称）。
    """
    base = resolve_job_dir(job, settings.jobs_dir)
    allowed = _declared_output_names(job) if declared is None else declared
    if not base.is_dir():
        return []
    # 声明名的祖先目录前缀集（reports/final.json → "reports"）：walk 只下探
    # 它们——runs/、点前缀暂存、未声明 scratch 整棵剪掉。
    declared_dirs = {str(seg) for name in allowed for seg in PurePosixPath(name).parents[:-1]}
    names: list[str] = []
    for root, dirs, files in os.walk(base, followlinks=False):
        rel_root = Path(root).relative_to(base).as_posix()
        in_root = "" if rel_root == "." else f"{rel_root}/"
        dirs[:] = [d for d in dirs if f"{in_root}{d}" in declared_dirs and d not in _PRUNED_DIRS]
        names.extend(
            relative
            for path in (Path(root) / name for name in files)
            if not path.is_symlink()
            and (relative := path.relative_to(base).as_posix()) in allowed
            and is_downloadable_artifact_name(relative)
        )
    return sorted(names)
