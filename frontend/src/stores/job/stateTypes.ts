import type { RerunByFailureCategoryAction } from '../../types/failureTypes'
import type { BatchOpMaybeAccepted } from '../../api/jobBatchOperationsApi'
import type {
  ApprovalBatchKind,
  BatchApprovalJobsResult,
} from '../../api/jobApprovalApi'

export type ContinueJobResult = Promise<{
  job_id: string
  operation: string
  status: string
  message?: string | null
  node_key?: string | null
  reason_code?: string | null
}>

export interface RerunByFailureActions {
  rerunByFailureCategory: RerunByFailureCategoryAction
}

export interface JobSupplementalActions extends RerunByFailureActions {
  batchApprovalLoading: boolean
  batchApproval: (
    workspaceId: string,
    kind: ApprovalBatchKind
  ) => Promise<BatchOpMaybeAccepted<BatchApprovalJobsResult>>
}
