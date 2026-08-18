import type { DagGraphEdge, DagGraphNode } from '../../components/dag/DagGraph'
import type { ChangeSummaryViewModel } from './workflowStudioChanges'

export type NodeChangeCounts = {
  added: number
  modified: number
  removed: number
  total: number
}

/** 顶栏计数：按 compare 的 node_changes 统计 added/modified/removed；无变更返回 null。 */
export function countNodeChanges(
  summary: ChangeSummaryViewModel | null
): NodeChangeCounts | null {
  if (!summary || summary.nodeChanges.length === 0) return null
  const counts: NodeChangeCounts = {
    added: 0,
    modified: 0,
    removed: 0,
    total: 0,
  }
  for (const change of summary.nodeChanges) {
    counts[change.type] += 1
    counts.total += 1
  }
  return counts
}

/**
 * 把 compare diff（draft vs active 基线）合并进 DAG 视图。画布展示的是基线
 * （active revision）图：modified/removed 节点本就在画布上，打角标（removed
 * 额外置灰虚线表示将被删除）；added 节点不在基线图里，以幽灵节点（虚线）
 * 补入画布，并补画 added 幽灵边保持布局位置上下文。无变更时原样返回。
 */
export function applyCompareChanges(
  nodes: DagGraphNode[],
  edges: DagGraphEdge[],
  summary: ChangeSummaryViewModel | null
): { nodes: DagGraphNode[]; edges: DagGraphEdge[] } {
  if (!summary || summary.nodeChanges.length === 0) return { nodes, edges }
  const changeByKey = new Map(
    summary.nodeChanges.map((change) => [change.nodeKey, change.type])
  )
  const nextNodes = nodes.map((node) => {
    const changeType = changeByKey.get(node.key)
    if (!changeType) return node
    return { ...node, changeType, ghost: changeType !== 'modified' }
  })
  for (const change of summary.nodeChanges) {
    if (change.type !== 'added') continue
    if (nextNodes.some((node) => node.key === change.nodeKey)) continue
    nextNodes.push({
      key: change.nodeKey,
      label: change.label,
      status: 'pending',
      created_at: '',
      changeType: 'added',
      ghost: true,
    })
  }
  const nodeKeys = new Set(nextNodes.map((node) => node.key))
  const ghostEdges = summary.edgeChanges
    .filter(
      (edge) =>
        edge.type === 'added' &&
        nodeKeys.has(edge.source) &&
        nodeKeys.has(edge.target) &&
        !edges.some((e) => e.from === edge.source && e.to === edge.target)
    )
    .map((edge) => ({ from: edge.source, to: edge.target, ghost: true }))
  return { nodes: nextNodes, edges: [...edges, ...ghostEdges] }
}
