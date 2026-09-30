import { useEffect, useState } from 'react'
import type { StudioMobilePanel } from './WorkflowStudioMobileNav'
import { useStudioNarrowViewport } from './useStudioNarrowViewport'

/** Agent Dock 开合 + 移动端页签的单一组合出口（#797 codex 复审轮，从
 * useWorkflowStudioPageView 拆出保体积预算）：顶栏开关与 Dock 关闭按钮都
 * 走 toggleAgent。Dock 的 Esc/折叠不走这里（折叠成 chip 仍可见，无需切页签）。
 * 复审轮 2：窄屏下开关以 Dock **实际可见性**（agentOpen && 页签=agent）为真值
 * ——agentOpen 与页签可能脱节（窄屏初始 agentOpen=true 而页签=graph、或从
 * Agent 页签切走），按 agentOpen 翻转要点两次才生效；按实际可见性切换：
 * 可见→关闭并回画布页签（不留空白工作区），不可见→打开并切 Agent 页签
 * （浮层才显示）。宽屏 agentOpen 即唯一真值（不碰页签）。
 * 复审轮 3：跨断点规范化——宽屏关 Dock 只翻 agentOpen，潜伏的 agent 页签
 * 进入窄屏会选中空内容页；进入窄屏时把这种组合归位回画布。
 * #804 抽屉化：「编辑节点」页签随分栏退役（节点编辑是全覆盖 Drawer），
 * 页签只剩 画布/Agent，mobilePanel 退回本 hook 的本地 state。 */
export function useAgentDockOpen() {
  const [agentOpen, setAgentOpen] = useState(true)
  const narrow = useStudioNarrowViewport()
  const [mobilePanel, setMobilePanel] = useState<StudioMobilePanel>('graph')
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
  // 跨断点规范化（#797 复审轮 3）：宽屏关 Dock 只翻 agentOpen，潜伏的
  // mobilePanel=agent 页签进入窄屏会选中一个空内容页（Dock 隐藏、画布/
  // 编辑被响应式 CSS 隐藏）——进入窄屏时把这种组合归位回画布。
  useEffect(() => {
    if (narrow && !agentOpen && mobilePanel === 'agent') {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- 跨断点归位是有意的 props/环境派生重置
      setMobilePanel('graph')
    }
  }, [narrow, agentOpen, mobilePanel, setMobilePanel])
  return {
    agentOpen,
    toggleAgent,
    dockVisible,
    mobilePanel,
    setMobilePanel,
    narrow,
  }
}
