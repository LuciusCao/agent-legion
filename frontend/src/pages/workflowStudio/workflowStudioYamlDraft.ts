import {
  dumpWorkflowYaml,
  parseWorkflowYaml,
  type WorkflowYamlNode,
} from './workflowStudioYamlDraft.parse'

export {
  parseWorkflowEdgeConditions,
  parseWorkflowLabel,
  parseWorkflowNode,
} from './workflowStudioYamlDraft.parse'
export type {
  WorkflowYamlEdge,
  WorkflowYamlNode,
  WorkflowYamlObject,
} from './workflowStudioYamlDraft.parse'

function patchNode(
  rawYaml: string,
  nodeKey: string,
  patch: (node: WorkflowYamlNode) => void
): string {
  const draft = parseWorkflowYaml(rawYaml)
  const node = draft.nodes?.[nodeKey]
  if (!node) throw new Error(`Node ${nodeKey} not found`)
  patch(node)
  return dumpWorkflowYaml(draft)
}

export function patchWorkflowNodeLabel(
  rawYaml: string,
  nodeKey: string,
  label: string
): string {
  return patchNode(rawYaml, nodeKey, (node) => {
    node.label = label
  })
}

export function patchWorkflowNodeInputs(
  rawYaml: string,
  nodeKey: string,
  inputs: string[]
): string {
  return patchNode(rawYaml, nodeKey, (node) => {
    node.inputs = inputs
  })
}

export function patchWorkflowNodeOutputs(
  rawYaml: string,
  nodeKey: string,
  outputs: string[]
): string {
  return patchNode(rawYaml, nodeKey, (node) => {
    node.outputs = outputs
  })
}

export function patchWorkflowNodeTerminalOutcome(
  rawYaml: string,
  nodeKey: string,
  outcome: string
): string {
  return patchNode(rawYaml, nodeKey, (node) => {
    if (!outcome.trim()) {
      delete node.terminal
      return
    }
    node.terminal = { outcome: outcome.trim() }
  })
}

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
    delete edge.condition
  } else {
    edge.condition = condition
  }
  return dumpWorkflowYaml(draft)
}
