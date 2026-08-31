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
  // 显式类型：'start' 为入口契约节点，'approval' 为人工决策门
  // （EXEC-APPROVAL-001），'code' | 'agent' 为显式执行类型（#284）；
  // 'node' 是 #284 前的遗留写法（后端 loader 归一化为 'code'），仅作读取容忍。
  type?: 'start' | 'node' | 'approval' | 'code' | 'agent'
  accepted_item_types?: string[]
  label?: string
  capability?: string
  // #76：节点级 skill 内容绑定。字符串形态（`skill: <key>`）与 mapping 形态
  // （`skill: {key, ref}`）都合法；ref 空 = 回落 skill_sources 默认 ref。
  skill?: string | { key?: string; ref?: string }
  after?: string[]
  inputs?: string[]
  outputs?: string[]
  terminal?: { outcome?: string }
  config_schema?: import('../../../types').ConfigSchema
  config?: { rework_target?: string; feedback_artifact?: string }
  // prettier-ignore
  execution?: { provider?: string; model?: string; thinking?: string; prompt?: string }
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
