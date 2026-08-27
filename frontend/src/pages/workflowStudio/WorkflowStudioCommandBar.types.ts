import type { WorkflowRevisionSummary } from '../../types'
import type { ChangeSummaryViewModel } from './workflowStudioChanges'
import type { DraftSaveState } from './useWorkflowDraftPersistence'

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
  /** 草稿自动保存状态：低噪暴露为 meta 文本 tooltip，不进 chip。 */
  draftSave?: DraftSaveState
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
