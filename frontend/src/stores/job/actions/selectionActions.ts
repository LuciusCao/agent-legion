import { getVisibleJobs } from '../selectors'
import type { JobFilterConfig, JobState, JobStoreSet } from '../state'
import { normalizeJobStatus } from '../state'

export function selectionActions(set: JobStoreSet, get: () => JobState) {
  return {
    setFilterConfig(config: Partial<JobFilterConfig>) {
      set((state) => ({
        filterConfig: { ...state.filterConfig, ...config },
        selectedIds: new Set(),
      }))
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
    selectUnpacked() {
      set((state) => {
        const visible = getVisibleJobs(state)
        const unpackedIds = visible
          .filter(
            (j) => normalizeJobStatus(j.status) === 'completed' && !j.packed
          )
          .map((j) => j.id)
        return { selectedIds: new Set(unpackedIds) }
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
