import type { JobSummary } from '../../../jobTypes'
import type { JobStoreSet } from '../state'
import * as fetchState from './fetchState'
import { getOrStartFetch } from './fetchInflight'

export function fetchActions(set: JobStoreSet) {
  return {
    async fetchJobs(workspaceId: string) {
      set(fetchState.startJobFetch(workspaceId))
      try {
        const data = await getOrStartFetch(workspaceId)
        set(fetchState.finishJobFetch(workspaceId, data.jobs))
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Failed to load jobs'
        set(fetchState.failJobFetch(workspaceId, msg))
      }
    },
    resetForWorkspace: (workspaceId: string) =>
      set(fetchState.resetForWorkspace(workspaceId)),
    setJobsAndFinishLoading: (jobs: JobSummary[]) =>
      set({ jobs, isLoading: false, error: null }),
    failJobFetch: (workspaceId: string, message: string) =>
      set(fetchState.failJobFetch(workspaceId, message)),
    setJobsSnapshot: (
      workspaceId: string,
      revision: number,
      jobs: JobSummary[]
    ) =>
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
