import type { WorkflowRevisionSummary } from '../../types'

export type WorkflowStudioRevisionProps = {
  activeRevision: WorkflowRevisionSummary | null
  viewMode: 'draft' | 'revision'
  selectedRevisionId: string | null
  readOnly: boolean
  hasPreservedDraft: boolean
  isLoadingRevision: boolean
  revisionLoadError: string | null
  selectRevision: (revisionId: string) => Promise<void>
  backToDraft: () => void
  useViewedRevisionAsDraft: () => void
}
