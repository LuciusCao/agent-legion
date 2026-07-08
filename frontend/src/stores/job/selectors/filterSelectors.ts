import type { JobState } from '../state'

export function selectFilteredJobIds(state: JobState): string[] {
  return state.filteredJobIds
}

export function selectFilterCounts(state: JobState) {
  return state.filterCounts
}
