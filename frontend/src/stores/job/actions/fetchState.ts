import type { JobState } from '../state'
import { isCurrentWorkspace } from './fetchStateHelpers'
import { filtersForWorkspace } from './workspaceFilterState'
import { computeFilterCounts } from '../filterLogic/incrementalFilters'
export { finishJobFetch, failJobFetch } from './fetchResult'

export const resetForWorkspace =
  (ws: string) =>
  (state: JobState): Partial<JobState> => {
    const keep = isCurrentWorkspace(state, ws)
    const filterConfig = filtersForWorkspace(state, ws)
    return {
      error: null,
      isLoading: true,
      jobs: [],
      jobsById: {},
      jobIds: [],
      jobIndexById: {},
      filteredJobIds: [],
      filterCounts: computeFilterCounts([], {}, filterConfig),
      optionAccumulator: state.optionAccumulator,
      jobsWorkspaceId: ws,
      selectedIds: keep ? state.selectedIds : new Set<string>(),
      filterConfig,
    }
  }

export const startJobFetch = resetForWorkspace
