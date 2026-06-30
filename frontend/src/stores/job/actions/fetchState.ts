import type { JobSummary } from '../../../jobTypes'
import type { JobState } from '../state'

function isCurrentWorkspace(state: JobState, workspaceId: string): boolean {
  return (
    state.jobsWorkspaceId === workspaceId ||
    (state.jobs.length > 0 &&
      state.jobs.every((job) => job.workspace_id === workspaceId))
  )
}

const baseReset = (ws: string, keepJobs: boolean) => (state: JobState) => ({
  error: null,
  isLoading: true,
  jobs: keepJobs && isCurrentWorkspace(state, ws) ? state.jobs : [],
  jobsWorkspaceId: ws,
  selectedIds: isCurrentWorkspace(state, ws)
    ? state.selectedIds
    : new Set<string>(),
})

export const startJobFetch = (ws: string) => baseReset(ws, false)
export const resetForWorkspace = (ws: string) => baseReset(ws, false)

export const finishJobFetch =
  (ws: string, jobs: JobSummary[]) => (state: JobState) =>
    state.jobsWorkspaceId === ws ? { jobs, error: null, isLoading: false } : {}

export const failJobFetch = (ws: string, msg: string) => (state: JobState) =>
  state.jobsWorkspaceId === ws ? { error: msg, isLoading: false, jobs: [] } : {}
