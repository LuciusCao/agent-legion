import type { JobSummary } from '../../../jobTypes'
import type { JobState } from '../state'

function isCurrentWorkspace(state: JobState, workspaceId: string): boolean {
  return (
    state.jobsWorkspaceId === workspaceId ||
    (state.jobs.length > 0 &&
      state.jobs.every((job) => job.workspace_id === workspaceId))
  )
}

export function startJobFetch(workspaceId: string) {
  return (state: JobState) => ({
    error: null,
    isLoading: true,
    jobs: [],
    jobsWorkspaceId: workspaceId,
    selectedIds: isCurrentWorkspace(state, workspaceId)
      ? state.selectedIds
      : new Set<string>(),
  })
}

export function finishJobFetch(workspaceId: string, jobs: JobSummary[]) {
  return (state: JobState) =>
    state.jobsWorkspaceId === workspaceId
      ? { jobs, error: null, isLoading: false }
      : {}
}

export function failJobFetch(workspaceId: string, message: string) {
  return (state: JobState) =>
    state.jobsWorkspaceId === workspaceId
      ? { error: message, isLoading: false, jobs: [] }
      : {}
}
