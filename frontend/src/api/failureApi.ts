import { api } from './core'
import type {
  FailedNodeRunsResponse,
  JobRerunByFailureRequest,
  JobRerunByFailureResponse,
} from '../types/failureTypes'

export async function fetchFailedNodeRuns(
  workspaceId: string,
  options?: { workflowKey?: string | null }
): Promise<FailedNodeRunsResponse> {
  const params = new URLSearchParams()
  if (options?.workflowKey) params.set('workflow_key', options.workflowKey)
  const query = params.toString()
  return api<FailedNodeRunsResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/failed-node-runs${query ? `?${query}` : ''}`
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
