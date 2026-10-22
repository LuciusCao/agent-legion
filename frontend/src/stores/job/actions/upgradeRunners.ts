import { batchUpgradeJobsWorkflow } from '../../../api/jobBatchUpgradeWorkflowApi'
import { upgradeJobWorkflow } from '../../../api/jobWorkflowUpgradeApi'
import {
  isJobBatchAccepted,
  type JobBatchOperationAccepted,
} from '../../../api/jobBatchOperationsApi'
import type { JobMutationResult, UpgradeMode } from '../../../types/jobTypes'
import type { JobState } from '../state'

/** Per-job upgrade loop for explicit selections: failures stay per-job results. */
async function upgradeEachJob(
  jobIds: string[],
  mode: UpgradeMode
): Promise<JobMutationResult[]> {
  const results: JobMutationResult[] = []
  for (const jobId of jobIds) {
    try {
      results.push(await upgradeJobWorkflow(jobId, mode))
    } catch (err) {
      results.push({
        job_id: jobId,
        operation: 'upgrade_workflow',
        status: 'failed',
        message: err instanceof Error ? err.message : String(err),
      } as JobMutationResult)
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
 * lists keep the legacy per-job loop. 大选择返回 202 受理句柄（#946），由
 * 调用方（upgradeActions）分流到任务中心通知。 */
export async function fetchUpgradeResults(
  state: JobState,
  workspaceId: string,
  jobIds?: string[],
  mode: UpgradeMode = 'clean'
): Promise<JobMutationResult[] | JobBatchOperationAccepted> {
  if (isAllMatchingUpgrade(state, jobIds) && state.selectionFilter) {
    const data = await batchUpgradeJobsWorkflow(
      workspaceId,
      {
        filter: state.selectionFilter,
        excludeIds: [...state.excludedIds],
      },
      mode
    )
    if (isJobBatchAccepted(data)) return data
    return data.results ?? []
  }
  return upgradeEachJob(jobIds ?? [], mode)
}
