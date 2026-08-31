import type { WorkflowRevisionSummary } from '../../../types'
import type { ChangeSummaryViewModel } from '../validation/workflowStudioChanges'

export type WorkflowStudioCommandBarProps = {
  revision: WorkflowRevisionSummary | null
  revisions: WorkflowRevisionSummary[]
  activeRevision: WorkflowRevisionSummary | null
  viewMode: 'draft' | 'revision'
  dirty: boolean
  readOnly: boolean
  hasPreservedDraft: boolean
  compareSummary: ChangeSummaryViewModel | null
  compareState: 'idle' | 'loading' | 'ready' | 'error'
  actionState: 'idle' | 'validating' | 'publishing'
  canSubmit: boolean
  canPublish: boolean
  selectedRevisionId?: string | null
  isLoadingRevision?: boolean
  revisionLoadError?: string | null
  onSelectRevision: (revisionId: string) => void
  onValidate: () => void
  onPublish: () => void
  onReset: () => void
  onShowChanges: () => void
  backToDraft: () => void
  useViewedRevisionAsDraft: () => void
}
