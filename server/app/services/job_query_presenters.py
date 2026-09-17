import os
from pathlib import Path

from server.app.services.job_artifact_names import NON_ARTIFACT_DIR_NAMES
from server.app.services.job_node_ordering import effective_after, ordered_job_nodes
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.definition import WorkflowDefinition


def job_nodes_with_definition(
    nodes: list[dict],
    definition: WorkflowDefinition,
) -> list[dict]:
    return [
        {
            **node,
            "label": definition.nodes[node["node_key"]].label
            if node["node_key"] in definition.nodes
            else node["node_key"],
            "capability": definition.nodes[node["node_key"]].capability
            if node["node_key"] in definition.nodes
            else node["node_key"],
            "after": effective_after(definition, node["node_key"]),
            "inputs": definition.nodes[node["node_key"]].inputs
            if node["node_key"] in definition.nodes
            else [],
            "outputs": definition.nodes[node["node_key"]].outputs
            if node["node_key"] in definition.nodes
            else [],
        }
        for node in ordered_job_nodes(nodes, definition)
    ]


def node_summary(
    node: dict,
    definition: WorkflowDefinition,
) -> dict:
    node_key = str(node["node_key"])
    label = definition.nodes[node_key].label if node_key in definition.nodes else node_key
    return {
        "node_key": node_key,
        "label": label,
        "status": str(node["status"]),
        "error_message": str(node.get("error_message", "")),
    }


def artifact_names(job: dict, settings: Settings) -> list[str]:
    base = resolve_job_dir(job, settings.jobs_dir)
    if not base.exists():
        return []
    return sorted(path.name for path in base.iterdir() if path.is_file())


# 剪枝名单与下载侧白名单的单一事实来源在 job_artifact_names（#631 攻击
# 复审 M2：清单剪枝与 serve 拒绝必须同一份规则）。
_NON_ARTIFACT_DIR_NAMES = NON_ARTIFACT_DIR_NAMES


def artifact_names_deep(job: dict, settings: Settings) -> list[str]:
    """Recursive variant of ``artifact_names`` for read surfaces that serve
    job-dir-relative subpath names (#631 review P2-1): declared outputs like
    ``reports/final.json`` land in subdirectories, which the root-only scan
    never sees.

    Non-artifact subtrees (``runs/``, dot-directories) are pruned; symlinked
    directories are not followed (os.walk default), so a planted link cannot
    enumerate files outside the job_dir — serving goes through the
    containment-checked ``JobArtifactService._artifact_path`` anyway.
    """
    base = resolve_job_dir(job, settings.jobs_dir)
    if not base.is_dir():
        return []
    names: list[str] = []
    for root, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = [d for d in dirs if d not in _NON_ARTIFACT_DIR_NAMES and not d.startswith(".")]
        names.extend((Path(root) / name).relative_to(base).as_posix() for name in files)
    return sorted(names)
