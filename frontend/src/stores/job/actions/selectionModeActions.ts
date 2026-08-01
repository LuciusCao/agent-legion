import { fetchJobFacets } from '../../../api'
import { useWorkspaceStore } from '../../workspaceStore'
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
      const failed = workspaceId
        ? useWorkspaceStore.getState().workspaceStats[workspaceId]?.job_stats
            ?.failed
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
