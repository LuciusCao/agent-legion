import type { JobState } from '../state'
import { filterCountsFromFacets } from '../filterLogic/facets'

export function selectFilteredJobIds(state: JobState): string[] {
  return state.filteredJobIds
}

export function selectFilterCounts(state: JobState) {
  return state.facets
    ? filterCountsFromFacets(state.facets)
    : state.filterCounts
}
