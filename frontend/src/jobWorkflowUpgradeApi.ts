import { api } from './api'
import type { JobMutationResult } from './jobTypes'

export async function upgradeJobWorkflow(
  jobId: string
): Promise<JobMutationResult> {
  return api<JobMutationResult>(
    `/api/jobs/${encodeURIComponent(jobId)}/upgrade-workflow`,
    { method: 'POST' }
  )
}
