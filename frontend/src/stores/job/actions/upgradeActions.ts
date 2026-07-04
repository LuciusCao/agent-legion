import { upgradeJobWorkflow } from '../../../jobWorkflowUpgradeApi'
import type { JobMutationResult } from '../../../jobTypes'
import { useUiStore } from '../../uiStore'
import {
  countMutationResults,
  makeMutationToast,
  type JobState,
  type JobStoreSet,
} from '../state'

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
        const succeededIds = new Set(
          results.filter((r) => r.status === 'succeeded').map((r) => r.job_id)
        )
        set((state) => {
          const nextSelected = new Set(state.selectedIds)
          for (const id of succeededIds) {
            nextSelected.delete(id)
          }
          return {
            selectedIds: nextSelected,
            selectMode: nextSelected.size === 0 ? false : state.selectMode,
          }
        })
        const counts = countMutationResults(results)
        useUiStore
          .getState()
          .showToast(
            makeMutationToast('升级 workflow', counts),
            counts.failed > 0 ? 'error' : 'success'
          )
        await get().fetchJobs(workspaceId)
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
