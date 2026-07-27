import { rerunJobsByFailure } from '../../../api/failureApi'
import type {
  JobRerunByFailureRequest,
  RerunByFailureInput,
} from '../../../types/failureTypes'
import { useUiStore } from '../../uiStore'
import { applyMutationResults } from './mutationResults'
import type { JobState, JobStoreSet } from '../state'

export function rerunByFailureActions(set: JobStoreSet, get: () => JobState) {
  return {
    async rerunByFailureCategory(
      workspaceId: string,
      input: RerunByFailureInput
    ) {
      const ids = input.jobIds ?? Array.from(get().selectedIds)
      if (ids.length === 0) return { results: [] }
      set({ batchRerunLoading: true })
      try {
        const body: JobRerunByFailureRequest = {
          category: input.category,
          strategy: 'auto',
          job_ids: ids,
        }
        const data = await rerunJobsByFailure(workspaceId, body)
        const results = data.results ?? []
        const hasUpstreamRerun = results.some(
          (r) =>
            r.node_key != null &&
            (r.rerun_nodes ?? []).some((node) => node !== r.node_key)
        )
        applyMutationResults(
          set,
          results,
          '重跑',
          hasUpstreamRerun ? '，含上游节点重跑' : ''
        )
        await get().fetchJobs(workspaceId)
        return data
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Rerun by failure failed'
        set({ error: message })
        useUiStore.getState().showToast(message, 'error')
        throw err
      } finally {
        set({ batchRerunLoading: false })
      }
    },
  }
}
