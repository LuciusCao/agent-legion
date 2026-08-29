import type { JobDetail } from '../../types/jobTypes'
import type { NodeCatalog } from '../../lib/nodeCatalog'
import { toDagEdges, toDagNodes } from './jobNodeHelpers'

export function toNodeCatalog(detail: JobDetail | null): NodeCatalog | null {
  if (!detail) return null
  return {
    key: detail.job.workflow_key,
    label: detail.job.workflow_key,
    nodes: detail.nodes.map((n) => ({
      key: n.node_key,
      label: n.label,
      capability: n.capability,
      after: n.after,
    })),
  }
}

export function deriveJobDetailPresentation(detail: JobDetail | null) {
  const nodes = detail ? toDagNodes(detail.nodes) : []
  const edges = detail ? toDagEdges(detail.nodes) : []
  const nodeCatalog = toNodeCatalog(detail)
  return {
    dagNodes: nodes,
    dagEdges: edges,
    nodeCatalog,
    workflowLabel: nodeCatalog?.label ?? detail?.job.workflow_key ?? '',
    outcome: detail?.job.outcome ?? '',
    workflowRevisionId: detail?.job.workflow_revision_id ?? '',
    currentWorkflowRevisionVersion:
      detail?.job.current_workflow_revision_version ?? null,
  }
}
