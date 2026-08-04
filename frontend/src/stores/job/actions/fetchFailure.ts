import type { JobState } from '../state'
import { computeFilterCounts } from '../filterLogic/incrementalFilters'

export const failJobFetch =
  (ws: string, msg: string) =>
  (state: JobState): Partial<JobState> =>
    state.jobsWorkspaceId === ws
      ? {
          error: msg,
          isLoading: false,
          jobs: [],
          jobsById: {},
          jobIds: [],
          jobIndexById: {},
          optionAccumulator: state.optionAccumulator,
          filteredJobIds: [],
          filterCounts: computeFilterCounts([], {}, state.filterConfig),
        }
      : {}
