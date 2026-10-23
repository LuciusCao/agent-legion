import type { DagEdge, DagGraphNode } from '../../components/dag/DagGraph'
import type { JobNode } from '../../types/jobTypes'

const VALID_STATUSES = new Set<DagGraphNode['status']>([
  'pending',
  'ready',
  'running',
  'completed',
  'failed',
  'stale',
  'not_applicable',
  'awaiting_approval',
])

export function normalizeStatus(status: string): DagGraphNode['status'] {
  if (VALID_STATUSES.has(status as DagGraphNode['status'])) {
    return status as DagGraphNode['status']
  }
  return 'pending'
}

export function computeNodeDuration(
  startedAt?: string | null,
  finishedAt?: string | null
): number | undefined {
  const start = startedAt ? new Date(startedAt).getTime() : NaN
  if (Number.isNaN(start)) return undefined
  if (finishedAt) {
    const end = new Date(finishedAt).getTime()
    if (Number.isNaN(end)) return undefined
    return (end - start) / 1000
  }
  return (Date.now() - start) / 1000
}

export function toDagNodes(nodes: JobNode[]): DagGraphNode[] {
  return nodes.map((n) => ({
    key: n.node_key,
    label: n.label || n.node_key,
    status: normalizeStatus(n.status),
    created_at: n.created_at,
    inputs: n.inputs,
    outputs: n.outputs,
    duration: computeNodeDuration(n.started_at, n.finished_at),
    // #1180：类型徽标跟现行 type 词汇——agent 节点（契约投影 agent_id
    // 非空）显示 agent；code 节点显示 code；route_error 节点（两者皆
    // None，#1128）不渲染类型徽标。executor_id 的 'code' 池键占位不再
    // 进入展示层。
    executionType: n.agent_id
      ? 'agent'
      : n.executor_kind === 'code'
        ? 'code'
        : null,
    agentId: n.agent_id ?? null,
    workerId: n.worker_id ?? null,
  }))
}

export function toDagEdges(nodes: JobNode[]): DagEdge[] {
  const edges: DagEdge[] = []
  nodes.forEach((node) => {
    if (node.after && Array.isArray(node.after)) {
      node.after.forEach((fromKey) => {
        edges.push({ from: fromKey, to: node.node_key })
      })
    }
  })
  return edges
}
