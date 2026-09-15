import { useState } from 'react'
import type { useWorkflowStudio } from './useWorkflowStudio'

type Studio = ReturnType<typeof useWorkflowStudio>

/** 画布区视图状态：DAG 常驻主视图；变更走右侧 Drawer，YAML 走全屏 Dialog。
 * #668：Agent 面板开合状态也提升到本层——开关收敛为 appbar（CommandBar）
 * 唯一入口，而面板布局在页面主体，两处经 StudioViewContext 共享本状态。 */
export function useWorkflowStudioPageView(studio: Studio) {
  const [dagFullscreenOpen, setDagFullscreenOpen] = useState(false)
  const [changesPanelOpen, setChangesPanelOpen] = useState(false)
  const [yamlEditorOpen, setYamlEditorOpen] = useState(false)
  const [agentOpen, setAgentOpen] = useState(true)
  const toggleAgent = () => setAgentOpen((open) => !open)
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
    agentOpen,
    toggleAgent,
    validateAndShowResult,
  }
}
