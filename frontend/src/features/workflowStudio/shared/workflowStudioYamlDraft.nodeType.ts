import {
  dumpWorkflowYaml,
  parseWorkflowYaml,
  type WorkflowYamlNode,
} from './workflowStudioYamlDraft.parse'

// 可切换的节点显式类型（#392）。start 是契约入口（每 DAG 恰一个、由
// loader 保证），不进选择器也不可切入/切出；读侧遗留 `node` 已在 parse
// 层归一化为 code。
export type SwitchableNodeType = 'code' | 'agent' | 'approval'

// Inspector 头部徽标（readOnly 态使用）：agent 无徽标（默认执行形态），
// approval 专属徽标，其余 code。
export function workflowNodeKindBadge(nodeType: string | undefined): string {
  if (nodeType === 'agent') return ''
  if (nodeType === 'approval') return 'approval'
  return 'code'
}

// 字段清洗规则镜像后端 loader 的类型禁令（唯一蓝本）：
// - approval 禁 capability/execution/skill/shard/reduce/config_schema
//   （server/app/workflows/approval_node.py _FORBIDDEN_APPROVAL_FIELDS），
//   config 只留 rework_target/feedback_artifact 白名单
//   （_ALLOWED_CONFIG_KEYS，未知键同样会被 loader 拒绝）。
// - code 禁 skill（EXEC-SKILL-NODE-001：code 节点不得声明 skill），
//   不剥 execution（code 节点可读 workflow 级 execution 默认值）。
// - agent 无额外剥离（skill/execution/capability 均合法；发布门禁另行
//   要求 capability 恰好解析到一个 published Agent）。
// approval 专属 config 键在 code/agent 上属于未知键（loader 对非 approval
// 节点的 config 不做白名单校验，但语义上无意义），一并剥除。
const APPROVAL_FORBIDDEN_FIELDS = [
  'capability',
  'execution',
  'skill',
  'shard',
  'reduce',
  'config_schema',
] as const
const APPROVAL_CONFIG_KEYS = ['rework_target', 'feedback_artifact'] as const

function sanitizeNodeForType(
  node: WorkflowYamlNode,
  targetType: SwitchableNodeType
): void {
  if (targetType === 'approval') {
    for (const field of APPROVAL_FORBIDDEN_FIELDS) {
      delete (node as Record<string, unknown>)[field]
    }
    if (node.config) {
      const config = node.config as Record<string, unknown>
      for (const key of Object.keys(config)) {
        if (!(APPROVAL_CONFIG_KEYS as readonly string[]).includes(key)) {
          delete config[key]
        }
      }
      if (Object.keys(config).length === 0) delete node.config
    }
    return
  }
  // code / agent：剥 approval 专属 config 键；code 另剥 skill。
  if (node.config) {
    const config = node.config as Record<string, unknown>
    for (const key of APPROVAL_CONFIG_KEYS) delete config[key]
    if (Object.keys(config).length === 0) delete node.config
  }
  if (targetType === 'code') delete node.skill
}

// 改写节点的显式执行类型（#284 → #392 通用化）：切换 type 的同时按目标
// 类型清洗字段，保证改写后的草稿不违反 loader 的类型禁令（否则下一次
// validate/publish 即被拒）。start 节点的类型不可改写。
export function patchWorkflowNodeType(
  rawYaml: string,
  nodeKey: string,
  nodeType: SwitchableNodeType
): string {
  const draft = parseWorkflowYaml(rawYaml)
  const node = draft.nodes?.[nodeKey]
  if (!node) throw new Error(`Node ${nodeKey} not found`)
  if (node.type === 'start') throw new Error(`Node ${nodeKey} is a start node`)
  node.type = nodeType
  sanitizeNodeForType(node, nodeType)
  return dumpWorkflowYaml(draft)
}
