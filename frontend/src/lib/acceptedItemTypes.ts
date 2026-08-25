import type { WorkflowDefinitionRecord } from '../types'

export type AcceptedItemType = 'material' | 'ref' | 'bundle'

/**
 * 入口契约：active revision 的 start 节点声明的 accepted_item_types。
 * 取不到定义（未发布 / 404）时按后端 DEFAULT 契约 `["material","ref"]`
 * 处理（刻意不含 `bundle`：存量 workspace 对 bundle 条目 fail-closed，
 * 需显式 opt-in），与后端 loader 对无 start 存量定义的自动注入语义一致
 * （EXEC-WORKFLOW-START-001）。
 */
export function acceptedItemTypes(
  workflow: WorkflowDefinitionRecord | null | undefined
): AcceptedItemType[] {
  const start = workflow?.nodes.find((node) => node.node_type === 'start')
  const types = start?.accepted_item_types
  return types && types.length > 0
    ? (types as AcceptedItemType[])
    : ['material', 'ref']
}
