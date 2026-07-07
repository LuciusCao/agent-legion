import type { JobSummary } from '../../../jobTypes'
import type { JobStoreSet } from '../state'

export function patchActions(set: JobStoreSet) {
  return {
    setJobsSnapshot: (workspaceId: string, revision: number, jobs: JobSummary[]) =>
      set({
        jobs,
        jobsById: Object.fromEntries(jobs.map((job) => [job.id, job])),
        jobIds: jobs.map((job) => job.id),
        jobsWorkspaceId: workspaceId,
        revision,
        isLoading: false,
        error: null,
      }),
    applyJobPatchBatch: (
      workspaceId: string,
      revision: number,
      patchJobs: JobSummary[],
      deletedJobIds: string[]
    ) =>
      set((state) => {
        if (state.jobsWorkspaceId !== workspaceId) return {}
        const deleted = new Set(deletedJobIds)
        const jobsById = { ...state.jobsById }
        for (const id of deleted) delete jobsById[id]
        for (const job of patchJobs) jobsById[job.id] = job
        const existingIds = state.jobIds.filter((id) => !deleted.has(id))
        const known = new Set(existingIds)
        const appended = patchJobs
          .map((job) => job.id)
          .filter((id) => !known.has(id))
        const jobIds = [...appended, ...existingIds]
        const jobs = jobIds.map((id) => jobsById[id]).filter(Boolean)
        return {
          jobsById,
          jobIds,
          jobs,
          revision,
          isLoading: false,
          error: null,
        }
      }),
  }
}
