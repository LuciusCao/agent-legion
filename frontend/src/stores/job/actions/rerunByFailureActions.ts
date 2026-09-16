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
        if (input.fromNodeKey) body.from_node_key = input.fromNodeKey
        const data = await rerunJobsByFailure(workspaceId, body)
        const results = data.results ?? []
        // rerun_nodes 在 upgrade-workflow 语境是数量（int），在
        // rerun-by-failure 语境是节点 key 列表——契约层声明为 unknown，
        // 此处按本端点语义窄化（issue #645 的子类覆写）。
        const rerunNodeKeys = (r: { rerun_nodes?: unknown }): string[] =>
          Array.isArray(r.rerun_nodes) ? (r.rerun_nodes as string[]) : []
        const hasUpstreamRerun = results.some(
          (r) =>
            r.node_key != null &&
            rerunNodeKeys(r).some((node) => node !== r.node_key)
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
