import { api } from './core'
import type {
  FailedNodeRunsResponse,
  JobRerunByFailureRequest,
  JobRerunByFailureResponse,
} from '../types/failureTypes'

/** 单页 failed-node-runs（#713 keyset 分页）；cursor 取上一页的 next_cursor。 */
export async function fetchFailedNodeRuns(
  workspaceId: string,
  cursor?: string
): Promise<FailedNodeRunsResponse> {
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''
  return api<FailedNodeRunsResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/failed-node-runs${query}`
  )
}

export async function rerunJobsByFailure(
  workspaceId: string,
  body: JobRerunByFailureRequest
): Promise<JobRerunByFailureResponse> {
  return api<JobRerunByFailureResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/rerun-by-failure`,
    { method: 'POST', body: JSON.stringify(body) }
  )
}
