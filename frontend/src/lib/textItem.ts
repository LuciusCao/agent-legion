import type { WorkflowDefinitionRecord } from '../types'

/** 与后端 run_text_items.TEXT_ITEM_MAX_BYTES 一致（UTF-8 字节数）。 */
export const TEXT_ITEM_MAX_BYTES = 64 * 1024
/** 与后端 run_text_items.DEFAULT_TEXT_FILENAME 一致。 */
export const DEFAULT_TEXT_FILENAME = '需求.md'

const encoder = new TextEncoder()

/** 文本条目的 UTF-8 字节数（后端按字节限长，中文一字三字节）。 */
export const textItemBytes = (text: string): number =>
  encoder.encode(text).length

export type StartTextInput = {
  label: string
  filename: string
  template: string
}

/** start 节点的 text_input 呈现配置；未声明 / 取不到定义时三项全空。 */
export function startTextInput(
  workflow: WorkflowDefinitionRecord | null | undefined
): StartTextInput {
  const start = workflow?.nodes.find((node) => node.node_type === 'start')
  const raw = start?.text_input
  return {
    label: raw?.label ?? '',
    filename: raw?.filename ?? '',
    template: raw?.template ?? '',
  }
}

export type ResolvedTextItem = {
  content: string
  filename: string
  bytes: number
  tooLong: boolean
  /** 用户还没动过预填模板（或改回了模板原文）——不算条目，提示先修改。 */
  untouchedTemplate: boolean
  ready: boolean
}

/**
 * 对话框的 text 状态解析：`text`/`filename` 为 null 表示用户未输入，回落到
 * start 节点 text_input 的模板 / 文件名；再回落到内置默认文件名。
 */
export function resolveTextItem(
  text: string | null,
  filename: string | null,
  config: StartTextInput
): ResolvedTextItem {
  const content = text ?? config.template
  const bytes = textItemBytes(content)
  const tooLong = bytes > TEXT_ITEM_MAX_BYTES
  const untouchedTemplate =
    config.template.trim().length > 0 &&
    content.trim() === config.template.trim()
  return {
    content,
    filename: (filename ?? config.filename).trim() || DEFAULT_TEXT_FILENAME,
    bytes,
    tooLong,
    untouchedTemplate,
    ready: content.trim().length > 0 && !tooLong && !untouchedTemplate,
  }
}

/** 提交给 runs API 的 text 条目。 */
export const textRunItem = (item: ResolvedTextItem) => ({
  type: 'text' as const,
  content: item.content,
  filename: item.filename,
})
