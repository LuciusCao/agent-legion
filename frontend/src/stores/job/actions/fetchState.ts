import type { JobSummary } from '../../../jobTypes'
import type { JobState } from '../state'

function isCurrentWorkspace(state: JobState, workspaceId: string): boolean {
  return (
    state.jobsWorkspaceId === workspaceId ||
    (state.jobs.length > 0 &&
      state.jobs.every((job) => job.workspace_id === workspaceId))
  )
}

export const startJobFetch = (ws: string) => (state: JobState) => ({
  error: null,
  isLoading: true,
  jobs: [],
  jobsWorkspaceId: ws,
  selectedIds: isCurrentWorkspace(state, ws)
    ? state.selectedIds
    : new Set<string>(),
})

// _state is unused because resetForWorkspace always clears selection.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export const resetForWorkspace = (ws: string) => (_state: JobState) => ({
  error: null,
  isLoading: true,
  jobs: [],
  jobsWorkspaceId: ws,
  selectedIds: new Set<string>(),
})

export const finishJobFetch =
  (ws: string, jobs: JobSummary[]) => (state: JobState) =>
    state.jobsWorkspaceId === ws ? { jobs, error: null, isLoading: false } : {}

export const failJobFetch = (ws: string, msg: string) => (state: JobState) =>
  state.jobsWorkspaceId === ws ? { error: msg, isLoading: false, jobs: [] } : {}
