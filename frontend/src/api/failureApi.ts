import { api } from './core'
import type {
  FailedNodeRunsResponse,
  JobRerunByFailureRequest,
  JobRerunByFailureResponse,
} from '../types/failureTypes'

export async function fetchFailedNodeRuns(
  workspaceId: string
): Promise<FailedNodeRunsResponse> {
  return api<FailedNodeRunsResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/failed-node-runs`
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
