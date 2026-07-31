import { rerunJobsByFailure } from '../../../api/failureApi'
import { targetBody } from '../../../api/batchTarget'
import type {
  JobRerunByFailureRequest,
  RerunByFailureInput,
} from '../../../types/failureTypes'
import { useUiStore } from '../../uiStore'
import { applyMutationResults } from './mutationResults'
import {
  refreshAfterBatchOperation,
  resolveOpTarget,
} from './selectionModeState'
import type { JobState, JobStoreSet } from '../state'

export function rerunByFailureActions(set: JobStoreSet, get: () => JobState) {
  return {
    async rerunByFailureCategory(
      workspaceId: string,
      input: RerunByFailureInput
    ) {
      const target = resolveOpTarget(get(), input.jobIds)
      if (!target) return { results: [] }
      set({ batchRerunLoading: true })
      try {
        const body: JobRerunByFailureRequest = {
          category: input.category,
          strategy: 'auto',
          ...targetBody(target),
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
        await refreshAfterBatchOperation(get, workspaceId)
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
