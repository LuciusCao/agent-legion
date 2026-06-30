import type { JobSummary } from '../../../jobTypes'
import type { JobStoreSet } from '../state'
import {
  failJobFetch,
  finishJobFetch,
  resetForWorkspace as resetForWorkspaceState,
  startJobFetch,
} from './fetchState'
import { getOrStartFetch } from './fetchInflight'

export function fetchActions(set: JobStoreSet) {
  return {
    async fetchJobs(workspaceId: string) {
      set(startJobFetch(workspaceId))
      try {
        const data = await getOrStartFetch(workspaceId)
        set(finishJobFetch(workspaceId, data.jobs))
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Failed to load jobs'
        set(failJobFetch(workspaceId, msg))
      }
    },
    resetForWorkspace: (workspaceId: string) =>
      set(resetForWorkspaceState(workspaceId)),
    setJobs: (jobs: JobSummary[]) => set({ jobs }),
  }
}
