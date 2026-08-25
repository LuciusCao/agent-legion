import type { components } from '../../generated/api'
import type { WorkflowDefinitionRecord, WorkflowNodeRecord } from '../../types'
type WorkflowConditionResponse =
  components['schemas']['WorkflowConditionResponse']
type WorkflowEdgeResponse = components['schemas']['WorkflowEdgeResponse']

export type ValidationGroups = {
  yaml: string[]
  schema: string[]
  structure: string[]
  executor: string[]
  revision: string[]
}

export type SelectedWorkflowNodeDetails = {
  node: WorkflowNodeRecord
  incoming: WorkflowEdgeResponse[]
  outgoing: WorkflowEdgeResponse[]
}

export function conditionLabel(
  condition: WorkflowConditionResponse | null | undefined
): string {
  if (!condition) return ''
  return `${condition.path} == ${JSON.stringify(condition.equals)}`
}

export { groupValidationErrors } from './workflowStudioValidationGroups'
export { ghostDraftNodeDetails } from './workflowStudioGhostNode'

export function isDefinitionDirty(
  originalDefinition: string,
  currentDefinition: string
): boolean {
  return (
    originalDefinition.replace(/\r\n/g, '\n') !==
    currentDefinition.replace(/\r\n/g, '\n')
  )
}

export function selectedNodeDetails(
  workflow: WorkflowDefinitionRecord | null,
  selectedNodeKey: string | null
): SelectedWorkflowNodeDetails | null {
  if (!workflow || !selectedNodeKey) return null
  const node = workflow.nodes.find(
    (candidate) => candidate.key === selectedNodeKey
  )
  if (!node) return null
  return {
    node,
    incoming: workflow.edges.filter((edge) => edge.target === selectedNodeKey),
    outgoing: workflow.edges.filter((edge) => edge.source === selectedNodeKey),
  }
}
