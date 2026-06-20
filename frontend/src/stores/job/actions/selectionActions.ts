import { getVisibleJobs } from '../selectors'
import type { JobState, JobStatus, JobStoreSet } from '../state'
import { normalizeJobStatus } from '../state'

export function selectionActions(set: JobStoreSet, get: () => JobState) {
  return {
    setStatusFilter(filter: JobStatus | 'all') {
      set({ statusFilter: filter, selectedIds: new Set() })
    },

    setSearchQuery(query: string) {
      set({ searchQuery: query, selectedIds: new Set() })
    },

    toggleSelectMode() {
      set((state) => ({
        selectMode: !state.selectMode,
        selectedIds: new Set(),
      }))
    },

    toggleSelect(id: string) {
      set((state) => {
        const next = new Set(state.selectedIds)
        if (next.has(id)) next.delete(id)
        else next.add(id)
        return { selectedIds: next }
      })
    },

    selectAll() {
      set((state) => {
        const visible = getVisibleJobs(state)
        return { selectedIds: new Set(visible.map((j) => j.id)) }
      })
    },

    selectFailed() {
      set((state) => {
        const failedIds = state.jobs
          .filter((j) => normalizeJobStatus(j.status) === 'failed')
          .map((j) => j.id)
        return { selectedIds: new Set(failedIds) }
      })
    },

    clearSelection() {
      set({ selectedIds: new Set() })
    },

    toggleExpand(id: string) {
      set((state) => ({ expandedId: state.expandedId === id ? null : id }))
    },

    getFilteredJobs() {
      return getVisibleJobs(get())
    },
  }
}
