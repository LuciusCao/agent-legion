import type { JobSummary } from '../../../jobTypes'
import type { JobStoreSet } from '../state'
import { failJobFetch, finishJobFetch, startJobFetch } from './fetchState'
import { getOrStartFetch } from './fetchInflight'

export function fetchActions(set: JobStoreSet) {
  return {
    async fetchJobs(workspaceId: string) {
      set(startJobFetch(workspaceId))
      try {
        const data = await getOrStartFetch(workspaceId)
        set(finishJobFetch(workspaceId, data.jobs))
      } catch (err) {
        set(
          failJobFetch(
            workspaceId,
            err instanceof Error ? err.message : 'Failed to load jobs'
          )
        )
      }
    },
    setJobs(jobs: JobSummary[]) {
      set({ jobs })
    },
  }
}
