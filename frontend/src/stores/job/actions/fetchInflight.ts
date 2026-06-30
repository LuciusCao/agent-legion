import { fetchJobs as apiFetchJobs } from '../../../api'
import type { JobSummary } from '../../../jobTypes'

const inflight = new Map<string, Promise<{ jobs: JobSummary[] }>>()

export function getOrStartFetch(
  workspaceId: string
): Promise<{ jobs: JobSummary[] }> {
  let p = inflight.get(workspaceId)
  if (!p) {
    p = apiFetchJobs(workspaceId).finally(() => inflight.delete(workspaceId))
    inflight.set(workspaceId, p)
  }
  return p
}
