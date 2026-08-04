import type { JobDetail } from '../../types/jobTypes'
import type { WorkflowDefinitionRecord } from '../../types'
import { toDagEdges, toDagNodes } from './jobNodeHelpers'
function toWorkflowDefinition(
  detail: JobDetail | null
): WorkflowDefinitionRecord | null {
  if (!detail) return null
  return {
    key: detail.job.workflow_key,
    label: detail.job.workflow_key,
    intake: { modes: [] },
    edges: [],
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

export function deriveJobDetailPresentation(detail: JobDetail | null) {
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
  const keyInfoPreviewable = detail
    ? detail.nodes.some(
        (n) => n.node_key === 'generate_key_info' && n.status === 'completed'
      )
    : false
  const possibleErrorsPreviewable = detail
    ? detail.nodes.some(
        (n) =>
          n.node_key === 'generate_possible_errors' && n.status === 'completed'
      )
    : false
  const TERMINAL_REVIEW_STATUSES = new Set(['completed', 'failed'])
  const keyInfoReviewAttempted = detail
    ? detail.nodes.some(
        (n) =>
          n.node_key === 'review_key_info' &&
          TERMINAL_REVIEW_STATUSES.has(n.status)
      )
    : false
  const possibleErrorsReviewAttempted = detail
    ? detail.nodes.some(
        (n) =>
          n.node_key === 'review_possible_errors' &&
          TERMINAL_REVIEW_STATUSES.has(n.status)
      )
    : false
  return {
    dagNodes: nodes,
    dagEdges: edges,
    workflowDefinition,
    workflowLabel: workflowDefinition?.label ?? detail?.job.workflow_key ?? '',
    outcome: detail?.job.outcome ?? '',
    workflowRevisionId: detail?.job.workflow_revision_id ?? '',
    currentWorkflowRevisionVersion:
      detail?.job.current_workflow_revision_version ?? null,
    questionArtifactRefreshKey,
    comprehensionRefreshKey,
    keyInfoPreviewable,
    possibleErrorsPreviewable,
    keyInfoReviewAttempted,
    possibleErrorsReviewAttempted,
  }
}
