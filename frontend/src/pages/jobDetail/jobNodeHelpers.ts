import type { DagEdge, DagGraphNode } from '../../components/DagGraph'
import type {
  JobDetailResponse,
  JobNodeRecord,
  WorkflowDefinitionRecord,
} from '../../types'

const VALID_STATUSES = new Set<DagGraphNode['status']>([
  'pending',
  'running',
  'completed',
  'failed',
  'stale',
])

const POLLING_STATUSES = new Set(['queued', 'running'])

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

export function toDagNodes(nodes: JobNodeRecord[]): DagGraphNode[] {
  return nodes.map((n) => ({
    key: n.node_key,
    label: n.label || n.node_key,
    status: normalizeStatus(n.status),
    created_at: n.created_at,
    inputs: n.inputs,
    outputs: n.outputs,
    duration: computeNodeDuration(n.started_at, n.finished_at),
    executorKind: (n.executor_kind as DagGraphNode['executorKind']) ?? null,
  }))
}

export function toDagEdges(nodes: JobNodeRecord[]): DagEdge[] {
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

export function toWorkflowDefinition(
  detail: JobDetailResponse | null
): WorkflowDefinitionRecord | null {
  if (!detail) return null
  return {
    key: detail.job.workflow_key,
    label: detail.job.workflow_key,
    intake: { modes: [] },
    nodes: detail.nodes.map((n) => ({
      key: n.node_key,
      label: n.label,
      after: n.after,
      capability: n.capability,
      inputs: n.inputs,
      outputs: n.outputs,
    })),
  }
}

export function deriveJobDetailPresentation(detail: JobDetailResponse | null) {
  const nodes = detail ? toDagNodes(detail.nodes) : []
  const edges = detail ? toDagEdges(detail.nodes) : []
  const workflowDefinition = toWorkflowDefinition(detail)
  const producer = detail?.nodes.find((node) =>
    node.outputs?.includes('questions.json')
  )
  const questionArtifactRefreshKey = producer
    ? [producer.status, producer.started_at, producer.finished_at].join(':')
    : ''
  const assembleNode = detail?.nodes.find(
    (node) => node.node_key === 'assemble_comprehension_info'
  )
  const reviewKeyInfoNode = detail?.nodes.find(
    (node) => node.node_key === 'review_key_info'
  )
  const reviewPossibleErrorsNode = detail?.nodes.find(
    (node) => node.node_key === 'review_possible_errors'
  )
  const comprehensionRefreshKey = [
    assembleNode?.status,
    assembleNode?.started_at,
    assembleNode?.finished_at,
    reviewKeyInfoNode?.status,
    reviewKeyInfoNode?.started_at,
    reviewKeyInfoNode?.finished_at,
    reviewPossibleErrorsNode?.status,
    reviewPossibleErrorsNode?.started_at,
    reviewPossibleErrorsNode?.finished_at,
  ].join(':')
  const comprehensionCompleted = detail
    ? detail.nodes.some(
        (n) =>
          n.node_key === 'assemble_comprehension_info' &&
          n.status === 'completed'
      ) ||
      (detail.nodes.some(
        (n) => n.node_key === 'review_key_info' && n.status === 'completed'
      ) &&
        detail.nodes.some(
          (n) =>
            n.node_key === 'review_possible_errors' && n.status === 'completed'
        ))
    : false
  return {
    dagNodes: nodes,
    dagEdges: edges,
    workflowDefinition,
    questionArtifactRefreshKey,
    comprehensionRefreshKey,
    comprehensionCompleted,
  }
}

export { POLLING_STATUSES }
