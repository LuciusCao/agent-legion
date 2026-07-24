import { api } from './core'
import type {
  BatchJobIdsRequest,
  BatchJobMutationResult,
  BatchRunToRequest,
  JobBatchRerunRequest,
  WorkspacePackageResult,
} from '../types/jobTypes'

export async function batchRunToJobs(
  workspaceId: string,
  targetNodeKey: string,
  jobIds: string[],
  startNodeKey?: string | null
): Promise<BatchJobMutationResult> {
  const body: BatchRunToRequest = {
    job_ids: jobIds,
    target_node_key: targetNodeKey,
  }
  if (startNodeKey != null) {
    body.start_node_key = startNodeKey
  }
  return api<BatchJobMutationResult>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/batch-run-to`,
    {
      method: 'POST',
      body: JSON.stringify(body),
    }
  )
}

export async function batchRerunJobs(
  workspaceId: string,
  nodeKey: string | null,
  jobIds: string[],
  options?: { fromFailedNode?: boolean }
): Promise<BatchJobMutationResult> {
  const body: JobBatchRerunRequest = {
    job_ids: jobIds,
    from_failed_node: options?.fromFailedNode ?? false,
  }
  if (nodeKey != null) body.node_key = nodeKey
  return api<BatchJobMutationResult>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/batch-rerun`,
    { method: 'POST', body: JSON.stringify(body) }
  )
}

export async function packageJobs(
  workspaceId: string,
  jobIds: string[]
): Promise<WorkspacePackageResult> {
  const body: BatchJobIdsRequest = { job_ids: jobIds }
  return api<WorkspacePackageResult>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/package`,
    {
      method: 'POST',
      body: JSON.stringify(body),
    }
  )
}

export async function batchDeleteJobs(
  workspaceId: string,
  jobIds: string[]
): Promise<BatchJobMutationResult> {
  const body: BatchJobIdsRequest = { job_ids: jobIds }
  return api<BatchJobMutationResult>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/batch`,
    {
      method: 'DELETE',
      body: JSON.stringify(body),
    }
  )
}
