import type { JobFilterConfig, JobState } from '../state'
import {
  computeFilterCounts,
  computeFilteredJobIds,
} from '../filterLogic/incrementalFilters'
import { clearedSelectionState } from './selectionModeState'

export function updateFilterConfig(
  state: JobState,
  config: Partial<JobFilterConfig>
): Partial<JobState> {
  const filterConfig = { ...state.filterConfig, ...config }
  return {
    filterConfig,
    ...clearedSelectionState(),
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
