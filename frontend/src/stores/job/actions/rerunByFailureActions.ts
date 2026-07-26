import { rerunJobsByFailure } from '../../../api/failureApi'
import type {
  JobRerunByFailureRequest,
  RerunByFailureInput,
} from '../../../types/failureTypes'
import { useUiStore } from '../../uiStore'
import { countMutationResults, makeMutationToast } from '../mutationHelpers'
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
        const hasUpstreamRerun = results.some(
          (r) =>
            r.node_key != null &&
            (r.rerun_nodes ?? []).some((node) => node !== r.node_key)
        )
        const toast =
          makeMutationToast('重跑', counts) +
          (hasUpstreamRerun ? '，含上游节点重跑' : '')
        useUiStore
          .getState()
          .showToast(toast, counts.failed > 0 ? 'error' : 'success')
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
