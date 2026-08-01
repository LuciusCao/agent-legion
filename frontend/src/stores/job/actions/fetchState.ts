import type { JobState } from '../state'
import { isCurrentWorkspace } from './fetchStateHelpers'
import { filtersForWorkspace } from './workspaceFilterState'
import { resetJobListForFilterChange } from './paginationState'
import { clearedSelectionState } from './selectionModeState'
export { finishJobFetch, failJobFetch } from './fetchResult'

export const resetForWorkspace =
  (ws: string) =>
  (state: JobState): Partial<JobState> => {
    const keep = isCurrentWorkspace(state, ws)
    const filterConfig = filtersForWorkspace(state, ws)
    return {
      ...resetJobListForFilterChange({ ...state, filterConfig }),
      jobsWorkspaceId: ws,
      ...(keep ? { selectedIds: state.selectedIds } : clearedSelectionState()),
      filterConfig,
    }
  }

export const startJobFetch = resetForWorkspace
