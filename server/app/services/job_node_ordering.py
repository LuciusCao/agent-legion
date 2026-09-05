from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.start_node import START_NODE_TYPE


def effective_after(definition: WorkflowDefinition, node_key: str) -> list[str]:
    # Start nodes are a definition-level concept and never enter job_nodes;
    # hide their derived `_start -> root` edges from the job view so the
    # frontend does not render phantom nodes for them. The workspace DAG view
    # no longer shares this helper: it derives predecessors straight from
    # definition.edges, because the fallback below would resurrect deleted
    # edges from the stale schema-v2 ``after`` echo (#424).
    edge_sources = [
        edge.source
        for edge in definition.edges
        if edge.target == node_key and definition.nodes[edge.source].node_type != START_NODE_TYPE
    ]
    if edge_sources:
        return edge_sources
    # v1 legacy fallback（保留，job 视图历史行为）：经 loader 解析的 v1
    # definition 在 _load_edges 里已把 after 回填进 definition.edges，故本臂
    # 并非 v1 遗留数据的唯一边来源，仅作防御兼容——schema v2 下 after 只是
    # 旧 echo，workspace DAG 不得回退（见上），job 视图维持原行为。
    if node_key in definition.nodes:
        return definition.nodes[node_key].after
    return []


def ordered_job_nodes(nodes: list[dict], definition: WorkflowDefinition) -> list[dict]:
    original_index = {key: index for index, key in enumerate(definition.nodes)}
    known_nodes = {node["node_key"]: node for node in nodes if node["node_key"] in definition.nodes}
    remaining_nodes = [node for node in nodes if node["node_key"] not in definition.nodes]
    children: dict[str, list[str]] = {key: [] for key in known_nodes}
    indegree: dict[str, int] = {key: 0 for key in known_nodes}
    for edge in definition.edges:
        if edge.source in known_nodes and edge.target in known_nodes:
            children[edge.source].append(edge.target)
            indegree[edge.target] += 1

    ready = sorted(
        [key for key, degree in indegree.items() if degree == 0],
        key=lambda key: original_index.get(key, len(original_index)),
    )
    ordered_keys: list[str] = []
    while ready:
        key = ready.pop(0)
        ordered_keys.append(key)
        for child in sorted(
            children[key],
            key=lambda child_key: original_index.get(child_key, len(original_index)),
        ):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
        ready.sort(key=lambda ready_key: original_index.get(ready_key, len(original_index)))

    if len(ordered_keys) < len(known_nodes):
        ordered_keys.extend(key for key in known_nodes if key not in ordered_keys)

    return [known_nodes[key] for key in ordered_keys] + remaining_nodes
