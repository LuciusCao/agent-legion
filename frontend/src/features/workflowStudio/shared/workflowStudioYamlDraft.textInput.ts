import {
  dumpWorkflowYaml,
  parseWorkflowYaml,
  type WorkflowYamlNode,
} from './workflowStudioYamlDraft.parse'

export type WorkflowTextInputDraft = {
  label: string
  filename: string
  template: string
}

export const EMPTY_TEXT_INPUT: WorkflowTextInputDraft = {
  label: '',
  filename: '',
  template: '',
}

/** YAML 里的可选三项 → API 记录形态（全字符串）；未声明返回 null。 */
export function normalizeTextInput(
  raw: WorkflowYamlNode['text_input']
): WorkflowTextInputDraft | null {
  if (!raw) return null
  return {
    label: raw.label ?? '',
    filename: raw.filename ?? '',
    template: raw.template ?? '',
  }
}

/**
 * start 节点的 text_input（「直接输入需求」的呈现配置：输入框标题 / 落盘文件名 /
 * 预填模板）。三项全空时删除整个键——与后端 loader「全空块 = 未声明」的
 * 归一化对称，避免 compare 出现幽灵变更。合成 _start 节点可能不在 draft
 * YAML 文本里，缺失时补建（同 patchWorkflowNodeAcceptedItemTypes）。
 */
export function patchWorkflowNodeTextInput(
  rawYaml: string,
  nodeKey: string,
  textInput: WorkflowTextInputDraft
): string {
  const d = parseWorkflowYaml(rawYaml)
  const node = ((d.nodes ??= {})[nodeKey] ??= { type: 'start' })
  const label = textInput.label.trim()
  const filename = textInput.filename.trim()
  const template = textInput.template
  if (!label && !filename && !template) {
    delete node.text_input
    return dumpWorkflowYaml(d)
  }
  node.text_input = {
    ...(label ? { label } : {}),
    ...(filename ? { filename } : {}),
    ...(template ? { template } : {}),
  }
  return dumpWorkflowYaml(d)
}
