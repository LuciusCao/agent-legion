import type { JobSummary } from '../../../jobTypes'
import type { JobState } from '../state'

export const normalizeJobs = (jobs: JobSummary[]) => ({
  jobs,
  jobsById: Object.fromEntries(jobs.map((job) => [job.id, job])),
  jobIds: jobs.map((job) => job.id),
})

export function isCurrentWorkspace(
  state: JobState,
  workspaceId: string
): boolean {
  return (
    state.jobsWorkspaceId === workspaceId ||
    (state.jobs.length > 0 &&
      state.jobs.every((job) => job.workspace_id === workspaceId))
  )
}
