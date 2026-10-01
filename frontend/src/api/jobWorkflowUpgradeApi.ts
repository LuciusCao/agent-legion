import { api } from './core'
import type { JobMutationResult, UpgradeMode } from '../types/jobTypes'

export async function upgradeJobWorkflow(
  jobId: string,
  mode: UpgradeMode = 'clean'
): Promise<JobMutationResult> {
  return api<JobMutationResult>(
    `/api/jobs/${encodeURIComponent(jobId)}/upgrade-workflow`,
    { method: 'POST', body: JSON.stringify({ mode }) }
  )
}
