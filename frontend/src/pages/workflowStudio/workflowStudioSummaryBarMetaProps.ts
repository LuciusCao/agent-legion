import type { WorkflowRevisionSummary } from '../../types'
import type { ChangeSummaryViewModel } from './workflowStudioChanges'

export type StudioSummaryBarMetaProps = {
  revision: WorkflowRevisionSummary | null
  dirty: boolean
  compareSummary: ChangeSummaryViewModel | null
  compareState: 'idle' | 'loading' | 'ready' | 'error'
}
