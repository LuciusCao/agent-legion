import { api } from './core'
import { targetBody, type BatchJobTarget } from './batchTarget'
import type { BatchJobMutationResult } from '../types/jobTypes'

export async function batchUpgradeJobsWorkflow(
  workspaceId: string,
  target: BatchJobTarget
): Promise<BatchJobMutationResult> {
  return api<BatchJobMutationResult>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/batch-upgrade-workflow`,
    {
      method: 'POST',
      body: JSON.stringify(targetBody(target)),
    }
  )
}
