import { createContext, useContext } from 'react'

export type NodeDetailPreviewKind = 'prompt' | 'skill'

// 节点详情 panel 内预览（运行 Prompt / 技能文件）的打开通道：WorkflowNodeDetailBody
// 持有预览状态并提供 setter，深层按钮（WorkflowAgentExecutionDetails）直接消费，
// 替代 Inspector → Body → Sections → ExecutionSection 的回调钻孔。默认 no-op：
// 脱离 DetailBody 渲染（如 Section 级测试）时点击按钮不产生副作用。
export const NodeDetailPreviewContext = createContext<
  (kind: NodeDetailPreviewKind) => void
>(() => {})

export function useShowNodeDetailPreview() {
  return useContext(NodeDetailPreviewContext)
}
