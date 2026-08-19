import { api } from '../api/core'
import type { JobListFilterParams, JobsPageResponse } from '../types/jobTypes'

export async function fetchJobsSnapshot(
  workspaceId: string,
  limit = 200,
  cursor?: string,
  filter?: JobListFilterParams
): Promise<JobsPageResponse> {
  const params = new URLSearchParams()
  params.set('limit', String(limit))
  if (cursor) params.set('cursor', cursor)
  appendFilterParams(params, filter)
  return api(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/snapshot?${params.toString()}`
  )
}
export function appendFilterParams(
  params: URLSearchParams,
  filter?: JobListFilterParams
): void {
  if (!filter) return
  const { status, search, active_node_key: nodeKey, packed, paused } = filter
  if (status) params.set('status', status)
  if (search) params.set('search', search)
  if (filter.workflow_version != null)
    params.set('workflow_version', String(filter.workflow_version))
  if (filter.workflow_version_none) params.set('workflow_version_none', 'true')
  if (nodeKey) params.set('active_node_key', nodeKey)
  if (packed != null) params.set('packed', String(packed))
  if (paused != null) params.set('paused', String(paused))
}
