import type { WorkflowNodeRecord } from '../../../types'
import { asConfigValue } from '../shared/workflowStudioYamlDraft.execution'

/** dispatch 必填键（EXEC-RUNTIME-DISPATCH-001）；thinking 可选，不进警告。 */
const REQUIRED_KEYS = ['provider', 'model'] as const

/**
 * 单个 agent 节点的 execution 缺口文案。节点记录只携带节点自身声明，
 * 不再合并 workflow 顶层默认值；非 agent 节点与已配齐的节点不警告。
 */
export function nodeExecutionWarning(
  node: WorkflowNodeRecord
): string | undefined {
  if (node.node_type !== 'agent') return undefined
  const missing = REQUIRED_KEYS.filter(
    (key) => !asConfigValue(node.execution?.[key]).trim()
  )
  return missing.length
    ? `缺 ${missing.join(' / ')}，该节点跑不起来`
    : undefined
}
