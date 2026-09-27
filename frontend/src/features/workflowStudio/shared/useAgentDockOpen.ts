import { useState } from 'react'
import type { useWorkflowStudio } from './useWorkflowStudio'
import { useWorkflowStudioMobilePanel } from './useWorkflowStudioMobilePanel'
import { useStudioNarrowViewport } from './useStudioNarrowViewport'

type Studio = ReturnType<typeof useWorkflowStudio>

/** Agent Dock 开合 + 移动端页签的单一组合出口（#797 codex 复审轮，从
 * useWorkflowStudioPageView 拆出保体积预算）：顶栏开关与 Dock 关闭按钮都
 * 走 toggleAgent。Dock 的 Esc/折叠不走这里（折叠成 chip 仍可见，无需切页签）。
 * 复审轮 2：窄屏下开关以 Dock **实际可见性**（agentOpen && 页签=agent）为真值
 * ——agentOpen 与页签可能脱节（窄屏初始 agentOpen=true 而页签=graph、或从
 * Agent 页签切走），按 agentOpen 翻转要点两次才生效；按实际可见性切换：
 * 可见→关闭并回画布页签（不留空白工作区），不可见→打开并切 Agent 页签
 * （浮层才显示）。宽屏 agentOpen 即唯一真值（不碰页签）。 */
export function useAgentDockOpen(studio: Studio) {
  const [agentOpen, setAgentOpen] = useState(true)
  const narrow = useStudioNarrowViewport()
  const { mobilePanel, setMobilePanel } = useWorkflowStudioMobilePanel(
    studio.selectedNodeKey,
    studio.focusNonce
  )
  // Dock 实际可见性：宽屏 = agentOpen；窄屏 = agentOpen 且页签在 agent。
  const dockVisible = narrow ? agentOpen && mobilePanel === 'agent' : agentOpen
  const toggleAgent = () => {
    if (!narrow) {
      setAgentOpen(!agentOpen)
      return
    }
    if (dockVisible) {
      // 窄屏关闭：收起 + 回画布页签（不留空白工作区）。
      setAgentOpen(false)
      setMobilePanel('graph')
    } else {
      // 窄屏打开：开 + 切 Agent 页签（浮层才显示）。
      setAgentOpen(true)
      if (mobilePanel !== 'agent') setMobilePanel('agent')
    }
  }
  return {
    agentOpen,
    toggleAgent,
    dockVisible,
    mobilePanel,
    setMobilePanel,
    narrow,
  }
}
