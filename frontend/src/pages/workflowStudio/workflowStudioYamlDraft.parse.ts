import yaml from 'js-yaml'

export type WorkflowYamlObject = {
  key?: string
  label?: string
  schema_version?: number
  intake?: unknown
  nodes?: Record<string, WorkflowYamlNode>
  edges?: WorkflowYamlEdge[]
}

export type WorkflowYamlNode = {
  label?: string
  capability?: string
  after?: string[]
  inputs?: string[]
  outputs?: string[]
  terminal?: { outcome?: string }
  execution?: {
    provider?: string
    model?: string
    thinking?: string
    prompt?: string
  }
}

export type WorkflowYamlEdge = {
  source?: string
  target?: string
  condition?: {
    artifact?: string
    path?: string
    equals?: string | number | boolean | null
  }
}

export function parseWorkflowYaml(rawYaml: string): WorkflowYamlObject {
  const parsed = yaml.load(rawYaml)
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('Workflow YAML must be a mapping')
  }
  return parsed as WorkflowYamlObject
}

export function dumpWorkflowYaml(value: WorkflowYamlObject): string {
  return yaml.dump(value, {
    lineWidth: 100,
    noRefs: true,
    sortKeys: false,
  })
}

export function parseWorkflowLabel(rawYaml: string): string | undefined {
  try {
    return parseWorkflowYaml(rawYaml).label
  } catch {
    return undefined
  }
}

export function parseWorkflowNode(
  rawYaml: string,
  nodeKey: string
): WorkflowYamlNode | undefined {
  try {
    const draft = parseWorkflowYaml(rawYaml)
    return draft.nodes?.[nodeKey]
  } catch {
    return undefined
  }
}

export function parseWorkflowEdgeConditions(
  rawYaml: string
): WorkflowYamlEdge[] {
  try {
    return parseWorkflowYaml(rawYaml).edges ?? []
  } catch {
    return []
  }
}
