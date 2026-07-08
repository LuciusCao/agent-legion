import type { JobState } from '../state'
import { isCurrentWorkspace } from './fetchStateHelpers'
import { computeFilterCounts } from '../filterLogic/incrementalFilters'
export { finishJobFetch, failJobFetch } from './fetchResult'

export const resetForWorkspace =
  (ws: string) =>
  (state: JobState): Partial<JobState> => {
    const keep = isCurrentWorkspace(state, ws)
    return {
      error: null,
      isLoading: true,
      jobs: [],
      jobsById: {},
      jobIds: [],
      jobIndexById: {},
      filteredJobIds: [],
      filterCounts: computeFilterCounts([], {}, state.filterConfig),
      optionAccumulator: state.optionAccumulator,
      jobsWorkspaceId: ws,
      selectedIds: keep ? state.selectedIds : new Set<string>(),
    }
  }

export const startJobFetch = resetForWorkspace
