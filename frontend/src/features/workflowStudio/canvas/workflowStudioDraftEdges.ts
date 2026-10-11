import type { components } from '../../../generated/api'
import type {
  WorkflowYamlEdge,
  WorkflowYamlNode,
  WorkflowYamlObject,
} from '../shared/workflowStudioYamlDraft.parse'

type WorkflowEdgeResponse = components['schemas']['WorkflowEdgeResponse']

// loader._load_edges 同构的草稿边集归一化（画布 record 与 ghost 详情共用
// 的单一归一化点，#1258 codex P2）：v1（schema_version 缺省/1）把节点
// after 物化为边、再并集顶层 edges——派生边在先，(from,to) 已在派生集合
// 的 raw 边跳过（其 when 条件与 loader 一样不生效）；v2+ 仅以顶层 edges
// 为准（after 是 echo 字段）。展示模型的额外防御：after 指向不存在的
// 节点/空 key 不派生边（loader 发布侧另拒未知依赖，画布不画幽灵端点）；
// 非数组 edges / 非 mapping 边按空集容忍（读侧不抛，形状守卫见
// workflowYamlToDefinitionRecord / inspectorNodeDetails）。
export function materializeDraftEdges(
  draft: WorkflowYamlObject
): WorkflowEdgeResponse[] {
  const nodes = isPlainObject(draft.nodes ?? {}) ? (draft.nodes ?? {}) : {}
  const rawEdges = (Array.isArray(draft.edges) ? draft.edges : []).filter(
    (edge): edge is WorkflowYamlEdge => isPlainObject(edge)
  )
  const materializeAfter =
    draft.schema_version == null || draft.schema_version === 1
  const known = new Set<string>()
  const edges: WorkflowEdgeResponse[] = []
  if (materializeAfter) {
    for (const [key, node] of Object.entries(nodes)) {
      for (const dep of (node as WorkflowYamlNode).after ?? []) {
        if (!dep || !(dep in nodes)) continue
        edges.push({ source: dep, target: key, condition: null })
        known.add(JSON.stringify([dep, key]))
      }
    }
  }
  for (const edge of rawEdges) {
    if (!edge.from || !edge.to) continue
    const pair = JSON.stringify([edge.from, edge.to])
    if (materializeAfter && known.has(pair)) continue
    known.add(pair)
    edges.push({
      source: edge.from,
      target: edge.to,
      condition: edge.when?.path
        ? {
            artifact: edge.when.artifact ?? '',
            path: edge.when.path,
            equals: edge.when.equals,
          }
        : null,
    })
  }
  return edges
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
