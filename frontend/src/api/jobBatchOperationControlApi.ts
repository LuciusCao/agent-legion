import { api } from './core'
import type { JobBatchOperation } from './jobBatchOperationsApi'

async function controlJobBatchOperation(
  workspaceId: string,
  opId: string,
  action: 'pause' | 'resume' | 'cancel'
): Promise<JobBatchOperation> {
  return api<JobBatchOperation>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/job-batch-operations/${encodeURIComponent(opId)}/${action}`,
    { method: 'POST' }
  )
}

export const pauseJobBatchOperation = (workspaceId: string, opId: string) =>
  controlJobBatchOperation(workspaceId, opId, 'pause')
export const resumeJobBatchOperation = (workspaceId: string, opId: string) =>
  controlJobBatchOperation(workspaceId, opId, 'resume')
export const cancelJobBatchOperation = (workspaceId: string, opId: string) =>
  controlJobBatchOperation(workspaceId, opId, 'cancel')
