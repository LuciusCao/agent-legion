import { api } from './core'
import type { JobFacetsResponse, JobListFilterParams } from '../types/jobTypes'
import { appendFilterParams } from './jobSnapshot'

export async function fetchJobFacets(
  workspaceId: string,
  filter?: JobListFilterParams
): Promise<JobFacetsResponse> {
  const params = new URLSearchParams()
  appendFilterParams(params, filter)
  const query = params.toString()
  return api(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/facets${query ? `?${query}` : ''}`
  )
}
