import type { useWorkflowStudio } from './useWorkflowStudio'

type StudioLayoutState = Omit<
  ReturnType<typeof useWorkflowStudio>,
  'validateDraft' | 'requestPublish' | 'resetDefinition'
>

export type StudioLayoutProps = StudioLayoutState & {
  dagFullscreenOpen: boolean
  setDagFullscreenOpen: (open: boolean) => void
  onValidate: () => void
  onPublish: () => void
  onReset: () => void
  onShowChanges: () => void
}
