import type { DagGraphEdge, DagGraphNode } from '../../components/DagGraph'
import type { WorkflowDefinitionRecord } from '../../types'
import { conditionLabel } from './workflowStudioModel'

export function buildDagNodes(
  workflow: WorkflowDefinitionRecord | null
): DagGraphNode[] {
  return (
    workflow?.nodes.map((node) => ({
      key: node.key,
      label: node.label,
      status: 'pending',
      created_at: '',
      capability: node.capability,
      terminalOutcome: node.terminal?.outcome,
      inputs: node.inputs,
      outputs: node.outputs,
    })) ?? []
  )
}

export function buildDagEdges(
  workflow: WorkflowDefinitionRecord | null
): DagGraphEdge[] {
  return (
    workflow?.edges.map((edge) => ({
      from: edge.source,
      to: edge.target,
      label: conditionLabel(edge.condition),
      conditional: Boolean(edge.condition),
    })) ?? []
  )
}
