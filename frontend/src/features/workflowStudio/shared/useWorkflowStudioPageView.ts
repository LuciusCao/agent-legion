import { useState } from 'react'
import { useAgentDockOpen } from './useAgentDockOpen'

/** 画布区视图状态：DAG 常驻主视图；变更走右侧 Drawer，YAML 走全屏 Dialog。
 * #668：Agent 面板开合状态提升到本层——开关收敛为 appbar（CommandBar）
 * 唯一入口，而面板布局在页面主体，两处经 StudioViewContext 共享本状态。
 * #795 PR②：chat 迁入 Dock 浮层；#797 codex 复审轮：移动端页签状态与
 * Agent 开合的组合出口在 useAgentDockOpen（本层只做转发）。
 * #804 定案：DAG 全屏 Dialog 与手动校验入口退役（校验改保存成功后自动
 * 静默执行，状态 chip 点击开变更抽屉）。 */
export function useWorkflowStudioPageView() {
  const [changesPanelOpen, setChangesPanelOpen] = useState(false)
  const [yamlEditorOpen, setYamlEditorOpen] = useState(false)
  const agentDock = useAgentDockOpen()
  return {
    changesPanelOpen,
    setChangesPanelOpen,
    yamlEditorOpen,
    setYamlEditorOpen,
    ...agentDock,
  }
}
