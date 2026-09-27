import { useState } from 'react'
import type { useWorkflowStudio } from './useWorkflowStudio'
import { useAgentDockOpen } from './useAgentDockOpen'

type Studio = ReturnType<typeof useWorkflowStudio>

/** 画布区视图状态：DAG 常驻主视图；变更走右侧 Drawer，YAML 走全屏 Dialog。
 * #668：Agent 面板开合状态提升到本层——开关收敛为 appbar（CommandBar）
 * 唯一入口，而面板布局在页面主体，两处经 StudioViewContext 共享本状态。
 * #795 PR②：chat 迁入 Dock 浮层；#797 codex 复审轮：移动端页签状态与
 * Agent 开合的组合出口在 useAgentDockOpen（本层只做转发）。 */
export function useWorkflowStudioPageView(studio: Studio) {
  const [dagFullscreenOpen, setDagFullscreenOpen] = useState(false)
  const [changesPanelOpen, setChangesPanelOpen] = useState(false)
  const [yamlEditorOpen, setYamlEditorOpen] = useState(false)
  const agentDock = useAgentDockOpen(studio)
  // 校验完成后打开变更面板（原切画布「变更」模式）。
  const validateAndShowResult = () =>
    studio.validateDraft().then(() => setChangesPanelOpen(true))
  return {
    dagFullscreenOpen,
    setDagFullscreenOpen,
    changesPanelOpen,
    setChangesPanelOpen,
    yamlEditorOpen,
    setYamlEditorOpen,
    ...agentDock,
    validateAndShowResult,
  }
}
