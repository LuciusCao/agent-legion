import { fetchJobFacets } from '../../../api'
import { queryClient } from '../../../lib/queryClient'
import { queryKeys } from '../../../lib/queryKeys'
import type { WorkspaceStats } from '../../../types/workspaceTypes'
import { toJobListFilterParams } from '../listFilterParams'
import type { JobState, JobStoreSet } from '../state'
import {
  clearedSelectionState,
  enterAllMatchingState,
  FAILED_SELECTION_FILTER,
  UNPACKED_SELECTION_FILTER,
} from './selectionModeState'

export function selectionModeActions(set: JobStoreSet, get: () => JobState) {
  return {
    selectAll() {
      set((state) =>
        enterAllMatchingState(
          toJobListFilterParams(state.filterConfig),
          state.totalJobs
        )
      )
    },
    selectFailed() {
      const workspaceId = get().jobsWorkspaceId
      // 非 hook 环境，直接读全局 queryClient 缓存；未缓存时返回 undefined，
      // 与原 store 缺失该 workspace stats 时的行为一致。
      const failed = workspaceId
        ? queryClient.getQueryData<WorkspaceStats>(
            queryKeys.workspaceStats(workspaceId)
          )?.job_stats?.failed
        : undefined
      set(enterAllMatchingState(FAILED_SELECTION_FILTER, failed ?? null))
    },
    selectUnpacked() {
      set(enterAllMatchingState(UNPACKED_SELECTION_FILTER, null))
      const workspaceId = get().jobsWorkspaceId
      if (workspaceId) void get().refreshSelectionCount(workspaceId)
    },
    clearSelection() {
      set(clearedSelectionState())
    },
    async refreshSelectionCount(workspaceId: string) {
      const filter = get().selectionFilter
      if (get().selectionMode !== 'allMatching' || !filter) return
      try {
        const facets = await fetchJobFacets(workspaceId, filter)
        const state = get()
        if (
          state.selectionMode === 'allMatching' &&
          state.selectionFilter === filter
        ) {
          set({ selectionCount: facets.total })
        }
      } catch {
        // Keep the unknown count; the UI falls back to loaded jobs.
      }
    },
  }
}
