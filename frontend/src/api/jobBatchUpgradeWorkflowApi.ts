import { api } from './core'
import { targetBody, type BatchJobTarget } from './batchTarget'
import type { BatchJobMutationResult, UpgradeMode } from '../types/jobTypes'

export async function batchUpgradeJobsWorkflow(
  workspaceId: string,
  target: BatchJobTarget,
  mode: UpgradeMode = 'clean'
): Promise<BatchJobMutationResult> {
  return api<BatchJobMutationResult>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/batch-upgrade-workflow`,
    {
      method: 'POST',
      body: JSON.stringify({ ...targetBody(target), mode }),
    }
  )
}
