import type { WorkflowNodeRecord } from '../../../types'
import {
  dumpWorkflowYaml,
  parseWorkflowYamlStrictNodes,
} from './workflowStudioYamlDraft.parse'

export type WorkflowTextInputDraft = NonNullable<
  WorkflowNodeRecord['text_input']
>

export const EMPTY_TEXT_INPUT: WorkflowTextInputDraft = {
  label: '',
  filename: '',
  template: '',
}

/** 不可信 YAML → 全字符串记录；null = 未声明，undefined = 非法形状。
 * 非法值不得强转字符串或丢弃后回写；调用方回退 published，保留原 YAML 修复。 */
export function normalizeTextInput(
  raw: unknown
): WorkflowTextInputDraft | null | undefined {
  if (raw == null) return null
  if (typeof raw !== 'object' || Array.isArray(raw)) return undefined
  if (Object.getPrototypeOf(raw) !== Object.prototype) return undefined
  const entries = Object.entries(raw)
  if (
    entries.some(
      ([key, value]) =>
        !Object.prototype.hasOwnProperty.call(EMPTY_TEXT_INPUT, key) ||
        (value != null && typeof value !== 'string')
    )
  )
    return undefined
  const value = { ...EMPTY_TEXT_INPUT, ...raw } as WorkflowTextInputDraft
  const label = (value.label ?? '').trim()
  const filename = (value.filename ?? '').trim()
  const template = value.template?.trim() ? value.template : ''
  return label || filename || template ? { label, filename, template } : null
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
  const d = parseWorkflowYamlStrictNodes(rawYaml)
  const node = ((d.nodes ??= {})[nodeKey] ??= { type: 'start' })
  const label = textInput.label.trim()
  const filename = textInput.filename.trim()
  const template = textInput.template
  if (!label && !filename && !template) {
    delete node.text_input
    return dumpWorkflowYaml(d)
  }
  if (normalizeTextInput(node.text_input) === undefined)
    throw new Error(
      'Invalid text_input; correct the YAML before editing fields'
    )
  node.text_input = {
    ...(label ? { label } : {}),
    ...(filename ? { filename } : {}),
    ...(template ? { template } : {}),
  }
  return dumpWorkflowYaml(d)
}
