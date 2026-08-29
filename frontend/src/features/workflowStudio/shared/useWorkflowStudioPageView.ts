import { useState } from 'react'
import type { useWorkflowStudio } from './useWorkflowStudio'

type Studio = ReturnType<typeof useWorkflowStudio>

/** 画布区视图状态：DAG 常驻主视图；变更走右侧 Drawer，YAML 走全屏 Dialog。 */
export function useWorkflowStudioPageView(studio: Studio) {
  const [dagFullscreenOpen, setDagFullscreenOpen] = useState(false)
  const [changesPanelOpen, setChangesPanelOpen] = useState(false)
  const [yamlEditorOpen, setYamlEditorOpen] = useState(false)
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
    validateAndShowResult,
  }
}
