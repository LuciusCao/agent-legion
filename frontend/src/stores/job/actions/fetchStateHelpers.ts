import type { JobSummary } from '../../../jobTypes'
import type { JobState } from '../state'
import { createOptionAccumulator } from '../filterLogic/optionAccumulator'

export const normalizeJobs = (jobs: JobSummary[]) => ({
  jobs,
  jobsById: Object.fromEntries(jobs.map((job) => [job.id, job])),
  jobIds: jobs.map((job) => job.id),
  jobIndexById: Object.fromEntries(jobs.map((job, index) => [job.id, index])),
  optionAccumulator: createOptionAccumulator(jobs),
})

export function isCurrentWorkspace(
  state: JobState,
  workspaceId: string
): boolean {
  return (
    state.jobsWorkspaceId === workspaceId ||
    (state.jobIds.length > 0 &&
      state.jobIds.every(
        (id) => state.jobsById[id]?.workspace_id === workspaceId
      ))
  )
}
