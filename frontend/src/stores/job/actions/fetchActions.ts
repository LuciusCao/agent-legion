import { fetchJobs as apiFetchJobs } from '../../../api'
import type { JobSummary } from '../../../jobTypes'
import type { JobStoreSet } from '../state'

export function fetchActions(set: JobStoreSet) {
  return {
    async fetchJobs(workspaceId: string) {
      set({ isLoading: true, error: null })
      try {
        const data = await apiFetchJobs(workspaceId)
        set({ jobs: data.jobs, error: null, isLoading: false })
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Failed to load jobs'
        set({ error: message, isLoading: false })
      }
    },

    setJobs(jobs: JobSummary[]) {
      set({ jobs })
    },
  }
}
