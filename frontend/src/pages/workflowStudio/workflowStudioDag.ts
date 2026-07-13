import type { DagGraphEdge, DagGraphNode } from '../../components/DagGraph'
import type { WorkflowDefinitionRecord } from '../../types'
import type { ExecutorDefinition } from '../../executorTypes'
import { conditionLabel } from './workflowStudioModel'
import { topologyBadges } from './workflowStudioDagBadges'

export function buildDagNodes(
  workflow: WorkflowDefinitionRecord | null,
  executors: ExecutorDefinition[] = []
): DagGraphNode[] {
  return (
    workflow?.nodes.map((node) => ({
      key: node.key,
      label: node.label,
      status: 'pending',
      created_at: '',
      capability: node.capability,
      executorKind: executors.find((executor) =>
        executor.capabilities.includes(node.capability)
      )?.kind,
      topologyBadges: topologyBadges(workflow, node.key),
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
