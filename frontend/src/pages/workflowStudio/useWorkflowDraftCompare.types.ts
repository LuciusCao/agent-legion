import type { components } from '../../generated/api'
import type { ChangeSummaryViewModel } from './workflowStudioChanges'

export type CompareState = 'idle' | 'loading' | 'ready' | 'error'
export type CompareResponse =
  components['schemas']['WorkflowDraftCompareResponse']
export type CompareError = components['schemas']['WorkflowDraftCompareError']

export type UseWorkflowDraftCompareResult = {
  compareState: CompareState
  compareResponse: CompareResponse | null
  compareErrors: CompareError[] | null
  compareSummary: ChangeSummaryViewModel | null
}
