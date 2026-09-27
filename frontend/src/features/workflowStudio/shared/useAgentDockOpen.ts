import { useState } from 'react'
import type { useWorkflowStudio } from './useWorkflowStudio'
import { useWorkflowStudioMobilePanel } from './useWorkflowStudioMobilePanel'
import { useStudioNarrowViewport } from './useStudioNarrowViewport'

type Studio = ReturnType<typeof useWorkflowStudio>

/** Agent Dock 开合 + 移动端页签的单一组合出口（#797 codex 复审轮，从
 * useWorkflowStudioPageView 拆出保体积预算）：顶栏开关与 Dock 关闭按钮都
 * 走 toggleAgent——窄屏下打开 Dock 同步切到 Agent 页签（否则浮层被响应式
 * CSS 藏起来看不见）、从 Agent 页签关闭 Dock 回画布页签（否则画布/编辑
 * 全隐藏留下空白页）。Dock 的 Esc/折叠不走这里（折叠成 chip 仍可见，无需
 * 切页签）。 */
export function useAgentDockOpen(studio: Studio) {
  const [agentOpen, setAgentOpen] = useState(true)
  const narrow = useStudioNarrowViewport()
  const { mobilePanel, setMobilePanel } = useWorkflowStudioMobilePanel(
    studio.selectedNodeKey,
    studio.focusNonce
  )
  const toggleAgent = () => {
    const next = !agentOpen
    setAgentOpen(next)
    if (!narrow) return
    if (next) {
      // 窄屏打开：切到 Agent 页签让 Dock 可见。
      if (mobilePanel !== 'agent') setMobilePanel('agent')
    } else if (mobilePanel === 'agent') {
      // 窄屏从 Agent 页签关闭：回画布，不留空白工作区。
      setMobilePanel('graph')
    }
  }
  return { agentOpen, toggleAgent, mobilePanel, setMobilePanel, narrow }
}
