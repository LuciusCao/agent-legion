import { api } from './core'
import type { JobMutationResult } from '../types/jobTypes'

export async function upgradeJobWorkflow(
  jobId: string
): Promise<JobMutationResult> {
  return api<JobMutationResult>(
    `/api/jobs/${encodeURIComponent(jobId)}/upgrade-workflow`,
    { method: 'POST' }
  )
}
