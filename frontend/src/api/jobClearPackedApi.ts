import { api } from './core'
import { targetBody, type BatchJobTarget } from './batchTarget'
import type { WorkspacePackageStatusResetResult } from '../types/jobTypes'

export async function clearJobsPackedStatus(
  workspaceId: string,
  target: BatchJobTarget
): Promise<WorkspacePackageStatusResetResult> {
  return api<WorkspacePackageStatusResetResult>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/clear-packed`,
    {
      method: 'POST',
      body: JSON.stringify(targetBody(target)),
    }
  )
}
