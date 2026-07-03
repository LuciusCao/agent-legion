import yaml from 'js-yaml'

type WorkflowYamlObject = {
  key?: string
  label?: string
  schema_version?: number
  intake?: unknown
  nodes?: Record<string, WorkflowYamlNode>
  edges?: WorkflowYamlEdge[]
}

type WorkflowYamlNode = {
  label?: string
  capability?: string
  after?: string[]
  inputs?: string[]
  outputs?: string[]
  terminal?: { outcome?: string }
}

type WorkflowYamlEdge = {
  source?: string
  target?: string
  condition?: {
    artifact?: string
    path?: string
    equals?: string | number | boolean | null
  }
}

function parseWorkflowYaml(rawYaml: string): WorkflowYamlObject {
  const parsed = yaml.load(rawYaml)
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('Workflow YAML must be a mapping')
  }
  return parsed as WorkflowYamlObject
}

function dumpWorkflowYaml(value: WorkflowYamlObject): string {
  return yaml.dump(value, {
    lineWidth: 100,
    noRefs: true,
    sortKeys: false,
  })
}

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
