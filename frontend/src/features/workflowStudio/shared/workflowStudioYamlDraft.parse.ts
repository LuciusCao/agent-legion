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
  // （`skill: {key, ref}`）都合法；ref 空 = latest（跟随仓库 HEAD，#322）。
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

/** 持久化 schema-v2 YAML 的边格式（loader._load_edges / revision_format
 * 序列化）：`edges: [{from, to, when}]`，`when` 内部为
 * {artifact, path, equals}（loader._load_condition）。注意与 API response
 * 的 WorkflowEdgeResponse（source/target/condition）区分：前者是 YAML 文本
 * 格式，后者是传输格式，映射在 workflowYamlDraftRecord / ghostNode 完成。 */
export type WorkflowYamlEdge = {
  from?: string
  to?: string
  when?: { artifact?: string; path?: string; equals?: unknown }
}

export function parseWorkflowYaml(rawYaml: string): WorkflowYamlObject {
  const parsed = yaml.load(rawYaml)
  if (!isMapping(parsed)) throw new Error('Workflow YAML must be a mapping')
  return parsed as WorkflowYamlObject
}

// 结构校验版解析（写路径专用）：语法合法但 nodes 是数组/字符串、或某
// 既有节点不是 mapping 的草稿，对象展开会把索引当节点键——写路径
// （如 appendWorkflowNode）覆盖保存前必须先过这道守卫。
const isMapping = (value: unknown) =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

export function parseWorkflowYamlStrictNodes(
  rawYaml: string
): WorkflowYamlObject {
  const draft = parseWorkflowYaml(rawYaml)
  const nodes = draft.nodes ?? {}
  if (!isMapping(nodes)) throw new Error('draft nodes is not a mapping')
  for (const [key, node] of Object.entries(nodes)) {
    if (!isMapping(node)) throw new Error(`draft node ${key} is not a mapping`)
  }
  return draft
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
