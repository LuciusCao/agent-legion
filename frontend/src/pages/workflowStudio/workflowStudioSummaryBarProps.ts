import type {
  WorkflowDefinitionRecord,
  WorkflowRevisionSummary,
} from '../../types'
import type { ChangeSummaryViewModel } from './workflowStudioChanges'

export type StudioSummaryBarProps = {
  workflow: WorkflowDefinitionRecord | null
  revision: WorkflowRevisionSummary | null
  compareSummary: ChangeSummaryViewModel | null
  compareState: 'idle' | 'loading' | 'ready' | 'error'
  dirty: boolean
  actionState: 'idle' | 'validating' | 'publishing'
  canSubmit: boolean
  canPublish: boolean
  onValidate: () => void
  onPublish: () => void
  onReset: () => void
}
