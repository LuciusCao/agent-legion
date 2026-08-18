import { batchUpgradeJobsWorkflow } from '../../../api/jobBatchUpgradeWorkflowApi'
import { upgradeJobWorkflow } from '../../../api/jobWorkflowUpgradeApi'
import type { JobMutationResult } from '../../../types/jobTypes'
import type { JobState } from '../state'

/** Per-job upgrade loop for explicit selections: failures stay per-job results. */
async function upgradeEachJob(jobIds: string[]): Promise<JobMutationResult[]> {
  const results: JobMutationResult[] = []
  for (const jobId of jobIds) {
    try {
      results.push(await upgradeJobWorkflow(jobId))
    } catch (err) {
      results.push({
        job_id: jobId,
        operation: 'upgrade_workflow',
        status: 'failed',
        message: err instanceof Error ? err.message : String(err),
      })
    }
  }
  return results
}

export function isAllMatchingUpgrade(
  state: JobState,
  jobIds?: string[]
): boolean {
  return (
    !jobIds && state.selectionMode === 'allMatching' && !!state.selectionFilter
  )
}

/** allMatching selections go through one server-side batch call; explicit id
 * lists keep the legacy per-job loop. */
export async function fetchUpgradeResults(
  state: JobState,
  workspaceId: string,
  jobIds?: string[]
): Promise<JobMutationResult[]> {
  if (isAllMatchingUpgrade(state, jobIds) && state.selectionFilter) {
    const data = await batchUpgradeJobsWorkflow(workspaceId, {
      filter: state.selectionFilter,
      excludeIds: [...state.excludedIds],
    })
    return data.results ?? []
  }
  return upgradeEachJob(jobIds ?? [])
}
