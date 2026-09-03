import type { WorkflowYamlNode } from './workflowStudioYamlDraft.parse'

// 类型切换的前置校验错误（抛 WorkflowNodeTypeSwitchError；调用侧转为
// toast，草稿保持原类型——多步变更必须先全部校验再统一应用，禁止落
// 入「type 已改、必填字段缺失」的半应用态，AGENTS.md L88）。
export class WorkflowNodeTypeSwitchError extends Error {}

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

export function sanitizeNodeForType(
  node: WorkflowYamlNode,
  sourceType: string | undefined,
  targetType: 'code' | 'agent' | 'approval'
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
//   边不算数）。判定源 = 节点 after ∪ 草稿 edges 的 to 侧（排除 from 为
//   start 的边）——手写 v2 YAML 用 edges 声明依赖时 after 只是 echo 字段，
//   单看 after 会误拦（与 validate_approval_edges 的物化 edges 同构）。
export function validateNodeTypeSwitch(
  draft: {
    nodes?: Record<string, WorkflowYamlNode>
    edges?: Array<{ from?: string; to?: string }>
  },
  node: WorkflowYamlNode,
  targetType: 'code' | 'agent' | 'approval'
): void {
  if (targetType !== 'approval' && !node.capability) {
    throw new WorkflowNodeTypeSwitchError(
      `切换为 ${targetType} 需要先在「基本设置」填写 capability（当前节点没有）`
    )
  }
  if (targetType === 'approval') {
    const startKeys = new Set(
      Object.entries(draft.nodes ?? [])
        .filter(([, n]) => n.type === 'start')
        .map(([key]) => key)
    )
    const upstream = new Set([
      ...(node.after ?? []),
      ...(draft.edges ?? [])
        .filter((edge) => edge.to && !startKeys.has(edge.from ?? ''))
        .map((edge) => edge.to as string),
    ])
    const executableUpstream = [...upstream].filter(
      (key) => !startKeys.has(key)
    )
    if (executableUpstream.length === 0) {
      throw new WorkflowNodeTypeSwitchError(
        '审批门至少需要一条来自可执行节点的入边；请先在「依赖关系」里接线'
      )
    }
  }
}
