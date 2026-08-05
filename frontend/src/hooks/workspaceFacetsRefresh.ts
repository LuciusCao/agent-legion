import { fetchJobFacets } from '../api'
import { toJobListFilterParams, useJobStore } from '../stores/jobStore'

/**
 * Best-effort facet refresh: filter counts and options derive from server
 * facets, so keep them fresh after job patch batches without refetching the
 * job list. Callers debounce this alongside the workspace stats refresh.
 */
export async function refreshJobFacets(
  workspaceId: string,
  statsOnly: boolean,
  isInactive: () => boolean
): Promise<void> {
  if (statsOnly) return
  const state = useJobStore.getState()
  if (state.jobsWorkspaceId !== workspaceId) return
  try {
    const facets = await fetchJobFacets(
      workspaceId,
      toJobListFilterParams(state.filterConfig)
    )
    if (isInactive()) return
    useJobStore.getState().setFacets(workspaceId, facets)
  } catch {
    // Keep the previous facets on failure; the next refresh retries.
  }
}
