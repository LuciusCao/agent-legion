import type { JobSummary } from '../../../jobTypes'
import type { JobStoreSet } from '../state'

export function appendActions(set: JobStoreSet) {
  return {
    appendJobsSnapshot: (workspaceId: string, jobs: JobSummary[]) =>
      set((state) => {
        if (state.jobsWorkspaceId !== workspaceId) return {}
        const known = new Set(state.jobIds)
        const added = jobs.filter((job) => !known.has(job.id))
        if (added.length === 0) return {}
        const jobsById = { ...state.jobsById }
        for (const job of added) jobsById[job.id] = job
        const jobIds = [...state.jobIds, ...added.map((job) => job.id)]
        return {
          jobsById,
          jobIds,
          jobs: jobIds.map((id) => jobsById[id]).filter(Boolean),
        }
      }),
  }
}
