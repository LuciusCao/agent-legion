// 渲染层输出截断（#1120 PR-2）：阈值常量命名对齐后端
// shared/output_truncation.py（#952）的 OUTPUT_* 先例。截断只做在渲染层、
// 不碰数据——extractWorkflowDraft / extractNodeCodeDrafts 的草稿卡解析依赖
// 完整 outputText（parseFirstJson），groupToolCalls 的输出保持全量。
export const OUTPUT_PREVIEW_CHAR_LIMIT = 4000

export function outputPreview(text: string): {
  preview: string
  truncated: boolean
} {
  if (text.length <= OUTPUT_PREVIEW_CHAR_LIMIT) {
    return { preview: text, truncated: false }
  }
  return { preview: text.slice(0, OUTPUT_PREVIEW_CHAR_LIMIT), truncated: true }
}
