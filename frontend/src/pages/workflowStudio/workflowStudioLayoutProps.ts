import type { useWorkflowStudio } from './useWorkflowStudio'
import type { StudioCanvasMode } from './useWorkflowStudioPageView'

type StudioLayoutState = Omit<
  ReturnType<typeof useWorkflowStudio>,
  'validateDraft' | 'requestPublish' | 'resetDefinition'
>

export type StudioLayoutProps = StudioLayoutState & {
  dagFullscreenOpen: boolean
  setDagFullscreenOpen: (open: boolean) => void
  canvasMode: StudioCanvasMode
  setCanvasMode: (mode: StudioCanvasMode) => void
  onValidate: () => void
  onPublish: () => void
  onReset: () => void
  onShowChanges: () => void
}
