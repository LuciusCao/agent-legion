import { upgradeJobWorkflow } from '../../../api/jobWorkflowUpgradeApi'
import type { JobMutationResult } from '../../../types/jobTypes'
import { useUiStore } from '../../uiStore'
import { applyMutationResults } from './mutationResults'
import { refreshAfterBatchOperation } from './selectionModeState'
import type { JobState, JobStoreSet } from '../state'

export function upgradeActions(set: JobStoreSet, get: () => JobState) {
  return {
    async batchUpgradeWorkflow(workspaceId: string, jobIds: string[]) {
      if (jobIds.length === 0) return { results: [] }
      set({ batchUpgradeWorkflowLoading: true })
      const results: JobMutationResult[] = []
      try {
        for (const jobId of jobIds) {
          try {
            const result = await upgradeJobWorkflow(jobId)
            results.push(result)
          } catch (err) {
            results.push({
              job_id: jobId,
              operation: 'upgrade_workflow',
              status: 'failed',
              message: err instanceof Error ? err.message : String(err),
            })
          }
        }
        applyMutationResults(set, results, '升级 workflow')
        await refreshAfterBatchOperation(get, workspaceId)
        return { results }
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Batch workflow upgrade failed'
        set({ error: message })
        useUiStore.getState().showToast(message, 'error')
        throw err
      } finally {
        set({ batchUpgradeWorkflowLoading: false })
      }
    },
  }
}
