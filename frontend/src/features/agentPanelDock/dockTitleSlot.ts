import { createContext, useContext } from 'react'

/** Dock 标题行插槽（#825）：AgentPanelDock 在标题与关闭按钮之间留一个
 * 容器节点，经 context 下发给内容子树；内容组件（会话管理条）用 portal
 * 渲染进标题行，状态仍留在原组件里（不必把聊天 hook 上提到 Dock 层）。
 * 不在 Dock 内渲染（单测直挂面板）时为 null，调用方回落行内渲染。 */
export const DockTitleSlotContext = createContext<HTMLElement | null>(null)

export function useDockTitleSlot(): HTMLElement | null {
  return useContext(DockTitleSlotContext)
}
