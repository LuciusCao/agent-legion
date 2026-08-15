import { useCallback, useState } from 'react'
import type { useWorkflowStudio } from './useWorkflowStudio'
import type { WorkflowStudioGlobalMode } from './WorkflowStudioGlobalDialog'

export type StudioPanelFocus = Record<'agents' | 'executors', string | null>

type Studio = ReturnType<typeof useWorkflowStudio>
type GlobalModeState = WorkflowStudioGlobalMode | null

const NO_FOCUS: StudioPanelFocus = { agents: null, executors: null }

export function useWorkflowStudioPageView(studio: Studio) {
  const [dagFullscreenOpen, setDagFullscreenOpen] = useState(false)
  const [globalMode, setGlobalMode] = useState<GlobalModeState>(null)
  const [panelFocus, setPanelFocus] = useState<StudioPanelFocus>(NO_FOCUS)
  // useCallback 稳住引用：StudioNavContext 的 value 经 useMemo 依赖它，
  // 否则 YAML 击键引发的重渲染会让全部 nav 消费者跟着重渲染。
  const openPanel = useCallback(
    (mode: 'agents' | 'executors', id: string | null = null) => {
      setPanelFocus({ ...NO_FOCUS, [mode]: id })
      setGlobalMode(mode)
    },
    []
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
    panelFocus,
    openPanel,
    validateAndShowResult,
  }
}
