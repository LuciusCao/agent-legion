import { api } from './core'
import type {
  BatchJobIdsRequest,
  WorkspacePackageStatusResetResult,
} from '../types/jobTypes'

export async function clearJobsPackedStatus(
  workspaceId: string,
  jobIds: string[]
): Promise<WorkspacePackageStatusResetResult> {
  const body: BatchJobIdsRequest = { job_ids: jobIds }
  return api<WorkspacePackageStatusResetResult>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/clear-packed`,
    {
      method: 'POST',
      body: JSON.stringify(body),
    }
  )
}
