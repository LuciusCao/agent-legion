import { initialFilterConfig } from '../initialState'
import type { JobState } from '../state'

export function filtersForWorkspace(state: JobState, workspaceId: string) {
  return state.jobsWorkspaceId === workspaceId
    ? state.filterConfig
    : { ...initialFilterConfig }
}
