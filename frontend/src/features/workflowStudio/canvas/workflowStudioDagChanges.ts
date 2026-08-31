import type * as DagTypes from '../../../components/dag/DagGraph'
import type { ChangeSummaryViewModel } from '../validation/workflowStudioChanges'

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
 * 把 compare diff（draft vs active 基线）合并进 DAG 视图（仅草稿模式调用）。
 * 画布数据源是草稿记录（解析失败时回退基线）：modified 打角标；added 相对
 * 已发布基线是「新」节点，统一幽灵（虚线）样式——已在画布上的打 added
 * 角标 + 幽灵样式，不在画布上的（如基线回退/摘要超前）补幽灵节点，并补画
 * added 幽灵边保持布局位置上下文；removed 在草稿里已删除即消失，基线回退
 * 时置灰虚线表示将被删除。无变更时原样返回。
 */
export function applyCompareChanges(
  nodes: DagTypes.DagGraphNode[],
  edges: DagTypes.DagGraphEdge[],
  summary: ChangeSummaryViewModel | null
): { nodes: DagTypes.DagGraphNode[]; edges: DagTypes.DagGraphEdge[] } {
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
