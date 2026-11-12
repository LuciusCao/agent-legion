import { api } from './core'
import { BULK_REQUEST_TIMEOUT } from './requestTimeout'
import { targetBody, type BatchJobTarget } from './batchTarget'
import type { BatchOpMaybeAccepted } from './jobBatchOperationsApi'
import type { WorkspacePackageStatusResetResult } from '../types/jobTypes'

export async function clearJobsPackedStatus(
  workspaceId: string,
  target: BatchJobTarget
): Promise<BatchOpMaybeAccepted<WorkspacePackageStatusResetResult>> {
  return api<BatchOpMaybeAccepted<WorkspacePackageStatusResetResult>>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/clear-packed`,
    {
      method: 'POST',
      body: JSON.stringify(targetBody(target)),
      timeoutMs: BULK_REQUEST_TIMEOUT,
    }
  )
}
