import { useUiStore } from '../../uiStore'
import { applyMutationResults } from './mutationResults'
import { refreshAfterBatchOperation } from './selectionModeState'
import { fetchUpgradeResults, isAllMatchingUpgrade } from './upgradeRunners'
import type { JobState, JobStoreSet } from '../state'

export function upgradeActions(set: JobStoreSet, get: () => JobState) {
  return {
    async batchUpgradeWorkflow(workspaceId: string, jobIds?: string[]) {
      const state = get()
      if (!isAllMatchingUpgrade(state, jobIds) && !jobIds?.length) {
        return { results: [] }
      }
      set({ batchUpgradeWorkflowLoading: true })
      try {
        const results = await fetchUpgradeResults(state, workspaceId, jobIds)
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
