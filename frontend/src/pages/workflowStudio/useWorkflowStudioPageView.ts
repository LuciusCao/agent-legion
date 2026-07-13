import { useState } from 'react'
import type { useWorkflowStudio } from './useWorkflowStudio'
import type { WorkflowStudioGlobalMode } from './WorkflowStudioGlobalDialog'

export function useWorkflowStudioPageView(
  studio: ReturnType<typeof useWorkflowStudio>
) {
  const [dagFullscreenOpen, setDagFullscreenOpen] = useState(false)
  const [globalMode, setGlobalMode] = useState<WorkflowStudioGlobalMode | null>(
    null
  )
  async function validateAndShowResult() {
    await studio.validateDraft()
    setGlobalMode('changes')
  }
  return {
    dagFullscreenOpen,
    setDagFullscreenOpen,
    globalMode,
    setGlobalMode,
    validateAndShowResult,
  }
}
