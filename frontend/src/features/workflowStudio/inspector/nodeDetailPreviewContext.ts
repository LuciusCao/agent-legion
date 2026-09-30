import { createContext, useContext } from 'react'

export type NodeDetailPreviewKind = 'prompt' | 'skill'

// 节点详情抽屉内预览（运行 Prompt / 技能文件）的打开通道：WorkflowNodeDetailDrawer
// 持有预览状态（预览返回条需要感知），经 WorkflowNodeDetailBody 提供 setter，
// 深层按钮（WorkflowAgentExecutionDetails / 预览面板内的技能芯片）直接消费，
// 替代 Inspector → Body → Sections → ExecutionSection 的回调钻孔。默认 no-op：
// 脱离抽屉渲染（如 Section 级测试）时点击按钮不产生副作用。
export const NodeDetailPreviewContext = createContext<
  (kind: NodeDetailPreviewKind) => void
>(() => {})

export function useShowNodeDetailPreview() {
  return useContext(NodeDetailPreviewContext)
}
