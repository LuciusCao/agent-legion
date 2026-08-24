import type { WorkflowDefinitionRecord } from '../types'

export type AcceptedItemType = 'material' | 'ref' | 'bundle'

/**
 * 入口契约：active revision 的 start 节点声明的 accepted_item_types。
 * 取不到定义（未发布 / 404）时按缺省全接受处理，与后端 loader 对
 * 无 start 存量定义的自动注入语义一致（EXEC-WORKFLOW-START-001）。
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
