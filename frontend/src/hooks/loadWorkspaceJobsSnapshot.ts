import { fetchJobFacets, fetchJobsSnapshot } from '../api'
import { toJobListFilterParams, useJobStore } from '../stores/jobStore'
import { useWorkspaceStore } from '../stores/workspaceStore'

/**
 * Load the first page of the server-filtered job list plus the facet counts.
 * The remaining pages stream in via loadMoreJobs as the user scrolls. If the
 * filter changes mid-load, a refreshFirstPage owns the list, so bail out.
 */
export async function loadWorkspaceJobsSnapshot(
  workspaceId: string,
  isStale: () => boolean
): Promise<void> {
  const filterConfig = useJobStore.getState().filterConfig
  const params = toJobListFilterParams(filterConfig)
  const first = await fetchJobsSnapshot(workspaceId, 500, undefined, params)
  if (isStale() || useJobStore.getState().filterConfig !== filterConfig) return
  useWorkspaceStore.getState().setWorkspaceStats(workspaceId, {
    ...useWorkspaceStore.getState().workspaceStats[workspaceId],
    job_stats: first.stats ?? {},
  })
  useJobStore
    .getState()
    .setJobsPage(
      workspaceId,
      first.revision,
      first.jobs,
      first.total,
      first.next_cursor
    )
  const facets = await fetchJobFacets(workspaceId, params)
  if (isStale() || useJobStore.getState().filterConfig !== filterConfig) return
  useJobStore.getState().setFacets(workspaceId, facets)
}
