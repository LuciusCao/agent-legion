import type { WorkflowNodeRecord } from '../../../types'
import { asConfigValue } from '../shared/workflowStudioYamlDraft.execution'
import { parseWorkflowNode } from '../shared/workflowStudioYamlDraft.parse'

export function readNodePromptDraft(
  definitionYaml: string,
  node: WorkflowNodeRecord
) {
  const draft = parseWorkflowNode(definitionYaml, node.key)
  // 草稿 prompt 可能是手写非字符串 junk（`prompt: 123`，合法 YAML 非法
  // 契约值）：按未配置归一，不得因 .trim() 抛异常（codex P1 缺陷族）。
  const customPrompt = asConfigValue(
    draft ? draft.execution?.prompt : node.execution?.prompt
  )
  // #513：拼接模式（追加/覆写）；非 overwrite 一律归一 append（含
  // junk 值与未声明）。
  const rawMode = asConfigValue(
    draft ? draft.execution?.prompt_mode : node.execution?.prompt_mode
  )
  const promptMode: 'append' | 'overwrite' =
    rawMode === 'overwrite' ? 'overwrite' : 'append'
  return { customPrompt, isDefault: customPrompt.trim() === '', promptMode }
}
