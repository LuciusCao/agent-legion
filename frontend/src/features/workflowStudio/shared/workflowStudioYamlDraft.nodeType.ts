import {
  dumpWorkflowYaml,
  parseWorkflowYaml,
  type WorkflowYamlNode,
} from './workflowStudioYamlDraft.parse'

// 可切换的节点显式类型（#392）。start 是契约入口（每 DAG 恰一个、由
// loader 保证），不进选择器也不可切入/切出；读侧遗留 `node` 已在 parse
// 层归一化为 code。
export type SwitchableNodeType = 'code' | 'agent' | 'approval'

// 类型切换的前置校验错误（抛 WorkflowNodeTypeSwitchError；调用侧转为
// toast，草稿保持原类型——多步变更必须先全部校验再统一应用，禁止落
// 入「type 已改、必填字段缺失」的半应用态，AGENTS.md L88）。
export class WorkflowNodeTypeSwitchError extends Error {}

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
// approval 专属 config 键只在源类型是 approval 时剥除（可执行节点的
// config 合法键由自身/Agent 的 config_schema 决定，rework_target 等并非
// 全局保留字——code↔agent 往返不得动它们）。
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
  sourceType: string | undefined,
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
  // code / agent：源是 approval 时剥审批专属 config 键；code 另剥 skill。
  if (sourceType === 'approval' && node.config) {
    const config = node.config as Record<string, unknown>
    for (const key of APPROVAL_CONFIG_KEYS) delete config[key]
    if (Object.keys(config).length === 0) delete node.config
  }
  if (targetType === 'code') delete node.skill
}

// 类型切换前置校验：目标类型必需的状态在写入前检查，不满足即抛错并
// 保留原类型（草稿可自动保存，半应用态会被持久化）。loader 侧对应：
// - code/agent 必须有非空 capability（loader.py 空 capability 拒绝；
//   approval 节点按契约无 capability，approval→code/agent 必须先补）。
// - approval 必须有来自可执行节点的入边（approval_node.py
//   validate_approval_edges；仅 start 驱动的根节点不满足，start 的合成
//   边不算数）。当前节点在 YAML 的 after 列表即声明边，loader 会把它
//   合成 edges；这里按同一语义检查 after 里存在非 start 的上游。
function validateNodeTypeSwitch(
  draft: ReturnType<typeof parseWorkflowYaml>,
  node: WorkflowYamlNode,
  targetType: SwitchableNodeType
): void {
  if (targetType !== 'approval' && !node.capability) {
    throw new WorkflowNodeTypeSwitchError(
      `切换为 ${targetType} 需要先在「基本设置」填写 capability（当前节点没有）`
    )
  }
  if (targetType === 'approval') {
    const upstream = node.after ?? []
    const executableUpstream = upstream.filter(
      (key) => draft.nodes?.[key]?.type !== 'start'
    )
    if (executableUpstream.length === 0) {
      throw new WorkflowNodeTypeSwitchError(
        '审批门至少需要一条来自可执行节点的入边；请先在「依赖关系」里接线'
      )
    }
  }
}

// 改写节点的显式执行类型（#284 → #392 通用化）：先做目标类型的前置
// 校验（capability / 入边），再切换 type 并按目标类型清洗字段，保证
// 改写后的草稿不违反 loader 的类型禁令（否则下一次 validate/publish
// 即被拒）。start 节点的类型不可改写。
export function patchWorkflowNodeType(
  rawYaml: string,
  nodeKey: string,
  nodeType: SwitchableNodeType
): string {
  const draft = parseWorkflowYaml(rawYaml)
  const node = draft.nodes?.[nodeKey]
  if (!node) throw new Error(`Node ${nodeKey} not found`)
  if (node.type === 'start') throw new Error(`Node ${nodeKey} is a start node`)
  validateNodeTypeSwitch(draft, node, nodeType)
  const sourceType = node.type ?? 'code'
  node.type = nodeType
  sanitizeNodeForType(node, sourceType, nodeType)
  return dumpWorkflowYaml(draft)
}
