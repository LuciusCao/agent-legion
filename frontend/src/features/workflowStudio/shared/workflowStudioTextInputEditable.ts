import {
  parseWorkflowNode,
  parseWorkflowYamlStrictNodes,
} from './workflowStudioYamlDraft.parse'
import {
  acceptedItemTypes,
  ITEM_TYPE_DISPLAY,
  type AcceptedItemType,
} from '../../../lib/acceptedItemTypes'
import {
  EMPTY_TEXT_INPUT,
  normalizeTextInput,
  patchWorkflowNodeTextInput,
} from './workflowStudioYamlDraft.textInput'

/** 明确取消 text 时允许整体删除非法块；不读取或重建块内字段。 */
export const clearDraftTextInput = (rawYaml: string, nodeKey: string) =>
  patchWorkflowNodeTextInput(rawYaml, nodeKey, EMPTY_TEXT_INPUT)

/** 契约也从当前草稿读取，不能把 published 回退的选项写回草稿。 */
export function readDraftStartTypes(
  rawYaml: string,
  nodeKey: string
): AcceptedItemType[] | null {
  if (!canEditStartNode(rawYaml, nodeKey, true)) return null
  const raw = parseWorkflowNode(rawYaml, nodeKey)?.accepted_item_types
  if (raw == null) return acceptedItemTypes(null)
  if (
    !Array.isArray(raw) ||
    raw.length === 0 ||
    raw.some(
      (value) =>
        typeof value !== 'string' ||
        !Object.prototype.hasOwnProperty.call(ITEM_TYPE_DISPLAY, value)
    )
  )
    return null
  return raw as AcceptedItemType[]
}

/** published 回退只供展示；编辑必须能定位到可安全回写的原始草稿节点。
 * 唯一可补建的缺省节点是 loader 合成的 _start，与 patch 契约一致。 */
export function canEditStartNode(
  rawYaml: string,
  nodeKey: string,
  allowInvalidTextInput = false
): boolean {
  try {
    const draft = parseWorkflowYamlStrictNodes(rawYaml)
    if (
      draft.edges?.some(
        (edge) => !edge || typeof edge !== 'object' || Array.isArray(edge)
      )
    )
      return false
    const node = draft.nodes?.[nodeKey]
    if (!node)
      return (
        nodeKey === '_start' &&
        !Object.values(draft.nodes ?? {}).some(
          (value) => value.type === 'start'
        )
      )
    return (
      node.type === 'start' &&
      (allowInvalidTextInput ||
        normalizeTextInput(node.text_input) !== undefined)
    )
  } catch {
    return false
  }
}
