import { api } from '../api/core'
import type { JobSummary } from '../types'

export async function fetchJobsSnapshot(
  workspaceId: string,
  limit = 200,
  cursor?: string
): Promise<{
  workspace_id: string
  revision: number
  stats: Record<string, number>
  jobs: JobSummary[]
  next_cursor: string | null
}> {
  const params = new URLSearchParams()
  params.set('limit', String(limit))
  if (cursor) params.set('cursor', cursor)
  return api(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/snapshot?${params.toString()}`
  )
}
