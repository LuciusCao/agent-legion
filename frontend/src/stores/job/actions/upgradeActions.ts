import { useUiStore } from '../../uiStore'
import type { UpgradeMode } from '../../../types/jobTypes'
import { applyMutationResults } from './mutationResults'
import { refreshAfterBatchOperation } from './selectionModeState'
import { fetchUpgradeResults, isAllMatchingUpgrade } from './upgradeRunners'
import type { JobState, JobStoreSet } from '../state'

export function upgradeActions(set: JobStoreSet, get: () => JobState) {
  return {
    async batchUpgradeWorkflow(
      workspaceId: string,
      jobIds?: string[],
      mode: UpgradeMode = 'clean'
    ) {
      const state = get()
      if (!isAllMatchingUpgrade(state, jobIds) && !jobIds?.length) {
        return { results: [] }
      }
      set({ batchUpgradeWorkflowLoading: true })
      try {
        const results = await fetchUpgradeResults(
          state,
          workspaceId,
          jobIds,
          mode
        )
        applyMutationResults(set, results, '升级 workflow')
        await refreshAfterBatchOperation(get, workspaceId)
        return { results }
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Batch workflow upgrade failed'
        set({ error: message })
        useUiStore.getState().showToast(message, 'error')
        // The batch may have partially succeeded server-side while the
        // response was lost — refresh the list so it reflects the
        // authoritative state. A failed refresh must not mask the original
        // error.
        await refreshAfterBatchOperation(get, workspaceId).catch(() => {})
        throw err
      } finally {
        set({ batchUpgradeWorkflowLoading: false })
      }
    },
  }
}
