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
  return types?.length ? (types as AcceptedItemType[]) : ['material', 'ref']
}

type ItemTypeDisplay = { label: string; description: string }

/**
 * 条目类型的用户视角展示信息（label + 一行说明），三处消费方共用一份：
 * Studio 入口契约编辑器（WorkflowNodeStartContractEditor）、readOnly 入口
 * 契约视图（WorkflowNodeStartSection）、「添加条目」提示条（AddItemsDialog）。
 * 新增条目类型在这里补一条，三处文案自动一致；key 顺序即规范顺序
 * （material/ref/bundle）。
 */
export const ITEM_TYPE_DISPLAY: Record<AcceptedItemType, ItemTypeDisplay> = {
  material: { label: '上传文件', description: '单个材料文件，浏览器直接上传' },
  ref: { label: '外部平台内容', description: '粘贴 ID 或链接引用外部平台内容' },
  bundle: { label: '整个文件夹', description: '保持目录结构，整体算一个条目' },
}

/** 条目类型的用户视角 label；未知类型回退原始值（前向兼容）。 */
export function itemTypeLabel(type: string): string {
  const hit = (ITEM_TYPE_DISPLAY as Record<string, ItemTypeDisplay>)[type]
  return hit?.label ?? type
}

/** 条目类型列表的展示串（顿号连接）；空列表回退占位符（未声明）。 */
export const itemTypeLabels = (types: readonly string[]): string =>
  types.map(itemTypeLabel).join('、') || '（未声明）'
