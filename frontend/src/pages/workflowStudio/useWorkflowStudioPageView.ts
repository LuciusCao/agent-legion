import { useState } from 'react'
import type { useWorkflowStudio } from './useWorkflowStudio'
import type { WorkflowStudioGlobalMode } from './WorkflowStudioGlobalDialog'

export type StudioPanelFocus = Record<'agents' | 'executors', string | null>

const NO_FOCUS: StudioPanelFocus = { agents: null, executors: null }

export function useWorkflowStudioPageView(
  studio: ReturnType<typeof useWorkflowStudio>
) {
  const [dagFullscreenOpen, setDagFullscreenOpen] = useState(false)
  const [globalMode, setGlobalMode] = useState<WorkflowStudioGlobalMode | null>(
    null
  )
  const [panelFocus, setPanelFocus] = useState<StudioPanelFocus>(NO_FOCUS)
  function openPanel(mode: 'agents' | 'executors', id: string | null = null) {
    setPanelFocus({ ...NO_FOCUS, [mode]: id })
    setGlobalMode(mode)
  }
  async function validateAndShowResult() {
    await studio.validateDraft()
    setGlobalMode('changes')
  }
  return {
    dagFullscreenOpen,
    setDagFullscreenOpen,
    globalMode,
    setGlobalMode,
    panelFocus,
    openPanel,
    validateAndShowResult,
  }
}
