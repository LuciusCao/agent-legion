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
            "after": definition.nodes[node["node_key"]].after
            if node["node_key"] in definition.nodes
            else [],
            "inputs": definition.nodes[node["node_key"]].inputs
            if node["node_key"] in definition.nodes
            else [],
            "outputs": definition.nodes[node["node_key"]].outputs
            if node["node_key"] in definition.nodes
            else [],
        }
        for node in nodes
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
