import { fetchJobs, fetchWorkspaceStats } from '../api'
import { useJobStore } from '../stores/jobStore'
import { useWorkspaceStore } from '../stores/workspaceStore'

export function mergeWorkspaceEventStats(
  workspaceId: string,
  stats: Record<string, number>
) {
  useWorkspaceStore.getState().setWorkspaceStats(workspaceId, {
    ...useWorkspaceStore.getState().workspaceStats[workspaceId],
    job_stats: stats,
  })
}

export async function refreshWorkspaceEvents(
  workspaceId: string,
  includeJobs: boolean,
  statsOnly: boolean,
  isInactive: () => boolean
) {
  try {
    const stats = await fetchWorkspaceStats(workspaceId)
    if (isInactive()) return
    useWorkspaceStore.getState().setWorkspaceStats(workspaceId, stats)
    if (!includeJobs || statsOnly) return
    const jobsData = await fetchJobs(workspaceId)
    if (isInactive()) return
    useJobStore.getState().setJobsAndFinishLoading(jobsData.jobs)
  } catch (err) {
    useJobStore
      .getState()
      .failJobFetch(
        workspaceId,
        err instanceof Error ? err.message : 'Failed to refresh jobs'
      )
  }
}
