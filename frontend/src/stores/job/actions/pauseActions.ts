import { batchPauseJobs, batchResumeJobs } from '../../../api/jobBatchPauseApi'
import { useUiStore } from '../../uiStore'
import { applyMutationResults } from './mutationResults'
import {
  refreshAfterBatchOperation,
  resolveBatchTarget,
} from './selectionModeState'
import type { JobState, JobStoreSet } from '../state'

export function pauseActions(set: JobStoreSet, get: () => JobState) {
  return {
    async batchPause(workspaceId: string, reason?: string) {
      const target = resolveBatchTarget(get())
      if (!target) return { results: [] }
      set({ batchPauseLoading: true })
      try {
        const data = await batchPauseJobs(workspaceId, target, reason)
        const results = data.results ?? []
        applyMutationResults(set, results, '暂停')
        await refreshAfterBatchOperation(get, workspaceId)
        return data
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Batch pause failed'
        set({ error: message })
        useUiStore.getState().showToast(message, 'error')
        throw err
      } finally {
        set({ batchPauseLoading: false })
      }
    },

    async batchResume(workspaceId: string) {
      const target = resolveBatchTarget(get())
      if (!target) return { results: [] }
      set({ batchResumeLoading: true })
      try {
        const data = await batchResumeJobs(workspaceId, target)
        const results = data.results ?? []
        applyMutationResults(set, results, '恢复')
        await refreshAfterBatchOperation(get, workspaceId)
        return data
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Batch resume failed'
        set({ error: message })
        useUiStore.getState().showToast(message, 'error')
        throw err
      } finally {
        set({ batchResumeLoading: false })
      }
    },
  }
}
