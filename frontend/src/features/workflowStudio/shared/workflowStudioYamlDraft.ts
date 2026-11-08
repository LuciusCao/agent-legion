import {
  dumpWorkflowYaml,
  parseWorkflowYaml,
} from './workflowStudioYamlDraft.parse'

export { parseWorkflowNode } from './workflowStudioYamlDraft.parse'
export type {
  WorkflowYamlEdge,
  WorkflowYamlNode,
  WorkflowYamlObject,
} from './workflowStudioYamlDraft.parse'
export {
  patchWorkflowNodeAcceptedItemTypes,
  patchWorkflowNodeCapability,
  patchWorkflowNodeInputs,
  patchWorkflowNodeLabel,
  patchWorkflowNodeOutputs,
  patchWorkflowNodeRequiresLabels,
  patchWorkflowNodeTerminalOutcome,
  patchWorkflowNodeTools,
} from './workflowStudioYamlDraft.node'

export function patchWorkflowLabel(rawYaml: string, label: string): string {
  const draft = parseWorkflowYaml(rawYaml)
  draft.label = label
  return dumpWorkflowYaml(draft)
}

export function patchWorkflowEdgeCondition(
  rawYaml: string,
  index: number,
  condition: {
    artifact?: string
    path: string
    equals: string | number | boolean | null
  } | null
): string {
  const draft = parseWorkflowYaml(rawYaml)
  const edge = draft.edges?.[index]
  if (!edge) throw new Error(`Edge at index ${index} not found`)
  if (!condition) {
    delete edge.when
  } else {
    edge.when = condition
  }
  return dumpWorkflowYaml(draft)
}
