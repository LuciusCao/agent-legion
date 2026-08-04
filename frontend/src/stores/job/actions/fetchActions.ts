import type { JobSummary } from '../../../types/jobTypes'
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
      set((state) => ({
        ...fetchState.finishJobFetch(state.jobsWorkspaceId || '', jobs)(state),
        revision: state.revision,
      })),
    failJobFetch: (workspaceId: string, message: string) =>
      set(fetchState.failJobFetch(workspaceId, message)),
  }
}
