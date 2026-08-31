import type {
  WorkflowDefinitionRecord,
  WorkflowNodeRecord,
} from '../../../types'
import type { WorkflowYamlNode } from '../shared/workflowStudioYamlDraft.parse'
import type { WorkflowYamlExecutionDefaults } from '../shared/workflowStudioYamlDraft.executionDefaults'

/**
 * #333：agent 节点 execution 解析链的前端轻量求值——节点 execution.* →
 * 顶层 execution 默认 → 缺失（与后端 loader.merge_execution_defaults +
 * dispatch 的 EXEC-RUNTIME-DISPATCH-001 fail-fast 同链，参考
 * server/app/agent_runtime/execution.py）。草稿记录侧先把顶层默认合并进
 * 非 start 节点（mergeNodeExecution，对齐 loader：published/revision
 * 快照里的节点 execution 即有效值），求值随后只读节点有效值。
 * 纯 code workflow 没有 agent 节点，警报集恒为空。
 */

type NodeExecution = NonNullable<WorkflowNodeRecord['execution']>

/**
 * 草稿节点 execution 还原 + 顶层默认合并（节点值优先；start 节点豁免，
 * prompt 仅节点级——对齐后端 loader.merge_execution_defaults）。节点未
 * 声明且默认全空时返回 undefined，保持记录形状与未配置时一致。
 */
export function mergeNodeExecution(
  node: WorkflowYamlNode,
  defaults: WorkflowYamlExecutionDefaults
): NodeExecution | undefined {
  const declared = node.execution
  if (
    node.type === 'start' ||
    (!defaults.provider && !defaults.model && !defaults.thinking)
  ) {
    return declared
      ? {
          provider: declared.provider ?? '',
          model: declared.model ?? '',
          thinking: declared.thinking ?? '',
          prompt: declared.prompt ?? '',
        }
      : undefined
  }
  return {
    provider: declared?.provider || defaults.provider || '',
    model: declared?.model || defaults.model || '',
    thinking: declared?.thinking || defaults.thinking || '',
    prompt: declared?.prompt ?? '',
  }
}

/** dispatch 必填键（EXEC-RUNTIME-DISPATCH-001）；thinking 可选，不进警告。 */
const REQUIRED_KEYS = ['provider', 'model'] as const

/**
 * 单个节点的 execution 缺口文案；非 agent 节点（code/start/approval 不读
 * execution）与已配齐的节点返回 undefined。输入的节点记录须为有效值
 * （草稿经 mergeNodeExecution、published 快照经后端 loader 合并）。
 */
export function nodeExecutionWarning(
  node: WorkflowNodeRecord
): string | undefined {
  if (node.node_type !== 'agent') return undefined
  const missing = REQUIRED_KEYS.filter(
    (key) => !(node.execution?.[key] ?? '').trim()
  )
  return missing.length
    ? `缺 ${missing.join(' / ')}，该节点跑不起来`
    : undefined
}

/**
 * 整体提示判定：workflow 有 agent 节点且顶层 execution 默认缺失
 * （provider/model 皆空，即没有可回落的默认层）。节点级缺口由节点徽标
 * 承载，这里只标顶层默认的缺席；纯 code workflow 不提示。
 */
export function topLevelExecutionMissing(
  workflow: WorkflowDefinitionRecord | null,
  defaults: WorkflowYamlExecutionDefaults
): boolean {
  const hasAgentNode = (workflow?.nodes ?? []).some(
    (node) => node.node_type === 'agent'
  )
  return hasAgentNode && !defaults.provider?.trim() && !defaults.model?.trim()
}
