import type { components } from '../../../generated/api'
import type { ChangeSummaryViewModel } from '../validation/workflowStudioChanges'

export type CompareState = 'idle' | 'loading' | 'ready' | 'error'
export type CompareResponse =
  components['schemas']['WorkflowDraftCompareResponse']
export type CompareError = components['schemas']['WorkflowDraftCompareError']

export type UseWorkflowDraftCompareResult = {
  compareState: CompareState
  compareResponse: CompareResponse | null
  compareErrors: CompareError[] | null
  compareSummary: ChangeSummaryViewModel | null
  /** 轮 6 H4：compare 传输失败（compareState='error'）的显式重试。 */
  retry: () => void
}
