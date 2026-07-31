import { api } from './core'
import { targetBody, type BatchJobTarget } from './batchTarget'
import type {
  BatchJobMutationResult,
  BatchRunToRequest,
  JobBatchRerunRequest,
  WorkspacePackageResult,
} from '../types/jobTypes'

export async function batchRunToJobs(
  workspaceId: string,
  targetNodeKey: string,
  target: BatchJobTarget,
  startNodeKey?: string | null
): Promise<BatchJobMutationResult> {
  const body: BatchRunToRequest = {
    ...targetBody(target),
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
  target: BatchJobTarget,
  options?: { fromFailedNode?: boolean }
): Promise<BatchJobMutationResult> {
  const body: JobBatchRerunRequest = {
    ...targetBody(target),
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
  target: BatchJobTarget
): Promise<WorkspacePackageResult> {
  return api<WorkspacePackageResult>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/package`,
    {
      method: 'POST',
      body: JSON.stringify(targetBody(target)),
    }
  )
}

export async function batchDeleteJobs(
  workspaceId: string,
  target: BatchJobTarget
): Promise<BatchJobMutationResult> {
  return api<BatchJobMutationResult>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/batch`,
    {
      method: 'DELETE',
      body: JSON.stringify(targetBody(target)),
    }
  )
}
