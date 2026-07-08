import type { JobFilterConfig, JobState } from '../state'
import {
  computeFilterCounts,
  computeFilteredJobIds,
} from '../filterLogic/incrementalFilters'

export function updateFilterConfig(
  state: JobState,
  config: Partial<JobFilterConfig>
): Partial<JobState> {
  const filterConfig = { ...state.filterConfig, ...config }
  return {
    filterConfig,
    selectedIds: new Set(),
    filteredJobIds: computeFilteredJobIds(
      state.jobIds,
      state.jobsById,
      filterConfig
    ),
    filterCounts: computeFilterCounts(
      state.jobIds,
      state.jobsById,
      filterConfig
    ),
  }
}
