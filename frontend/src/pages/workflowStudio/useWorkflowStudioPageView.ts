import { useState } from 'react'
import type { useWorkflowStudio } from './useWorkflowStudio'

/** 画布区三模式：DAG 画布 | YAML | 变更（原顶栏全局弹窗下沉而来）。 */
export type StudioCanvasMode = 'dag' | 'yaml' | 'changes'

type Studio = ReturnType<typeof useWorkflowStudio>

export function useWorkflowStudioPageView(studio: Studio) {
  const [dagFullscreenOpen, setDagFullscreenOpen] = useState(false)
  const [canvasMode, setCanvasMode] = useState<StudioCanvasMode>('dag')
  async function validateAndShowResult() {
    await studio.validateDraft()
    setCanvasMode('changes')
  }
  return {
    dagFullscreenOpen,
    setDagFullscreenOpen,
    canvasMode,
    setCanvasMode,
    validateAndShowResult,
  }
}
