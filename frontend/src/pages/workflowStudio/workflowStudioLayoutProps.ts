import type { useWorkflowStudio } from './useWorkflowStudio'
import type { ExecutorDefinition } from '../../executorTypes'

type StudioLayoutState = Omit<
  ReturnType<typeof useWorkflowStudio>,
  'executorCatalog' | 'validateDraft' | 'requestPublish' | 'resetDefinition'
>

export type StudioLayoutProps = StudioLayoutState & {
  executorCatalog: ExecutorDefinition[]
  dagFullscreenOpen: boolean
  setDagFullscreenOpen: (open: boolean) => void
  onValidate: () => void
  onPublish: () => void
  onReset: () => void
  onShowChanges: () => void
}
