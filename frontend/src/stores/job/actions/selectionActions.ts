import { getVisibleJobs } from '../selectors'
import type { JobFilterConfig, JobState, JobStoreSet } from '../state'
import { updateFilterConfig } from './filterSelectionState'
import { clearedSelectionState } from './selectionModeState'

export function selectionActions(set: JobStoreSet, get: () => JobState) {
  return {
    setFilterConfig(config: Partial<JobFilterConfig>) {
      set((state) => updateFilterConfig(state, config))
    },
    toggleSelectMode() {
      set((state) => ({
        selectMode: !state.selectMode,
        ...clearedSelectionState(),
      }))
    },
    toggleSelect(id: string) {
      set((state) => {
        if (state.selectionMode === 'allMatching') {
          const next = new Set(state.excludedIds)
          if (next.has(id)) next.delete(id)
          else next.add(id)
          return { excludedIds: next }
        }
        const next = new Set(state.selectedIds)
        if (next.has(id)) next.delete(id)
        else next.add(id)
        return { selectedIds: next }
      })
    },
    toggleExpand(id: string) {
      set((state) => ({ expandedId: state.expandedId === id ? null : id }))
    },
    getFilteredJobs() {
      return getVisibleJobs(get())
    },
  }
}
