import type { RerunByFailureCategoryAction } from '../../types/failureTypes'

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
