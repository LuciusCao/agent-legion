import type { JobSummary } from '../../../jobTypes'
import type { JobStoreSet } from '../state'

export function snapshotActions(set: JobStoreSet) {
  return {
    setJobsSnapshot: (
      workspaceId: string,
      revision: number,
      jobs: JobSummary[]
    ) =>
      set((state) => {
        if (
          (state.jobsWorkspaceId && state.jobsWorkspaceId !== workspaceId) ||
          revision < state.revision
        )
          return {}
        return {
          jobs,
          jobsById: Object.fromEntries(jobs.map((job) => [job.id, job])),
          jobIds: jobs.map((job) => job.id),
          jobsWorkspaceId: workspaceId,
          revision,
          isLoading: false,
          error: null,
        }
      }),
  }
}
