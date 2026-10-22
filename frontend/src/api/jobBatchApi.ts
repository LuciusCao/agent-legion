import { api } from './core'
import { BULK_REQUEST_TIMEOUT } from './requestTimeout'
import { targetBody, type BatchJobTarget } from './batchTarget'
import type { BatchOpMaybeAccepted } from './jobBatchOperationsApi'
import type {
  BatchJobMutationResult,
  BatchRunToRequest,
  JobBatchRerunRequest,
  WorkspacePackageResult,
} from '../types/jobTypes'

// 大选择（> AGENT_LEGION_BATCH_OPS_ASYNC_THRESHOLD，默认 1000）时这些端点
// 返回 202 受理句柄而非同步结果（#946）；调用方用 isJobBatchAccepted 分流，
// 受理分支的进度走任务中心，不再出「结束后一次性 toast」。

export async function batchRunToJobs(
  workspaceId: string,
  targetNodeKey: string,
  target: BatchJobTarget,
  startNodeKey?: string | null
): Promise<BatchOpMaybeAccepted<BatchJobMutationResult>> {
  const body: BatchRunToRequest = {
    ...targetBody(target),
    target_node_key: targetNodeKey,
  }
  if (startNodeKey != null) {
    body.start_node_key = startNodeKey
  }
  return api<BatchOpMaybeAccepted<BatchJobMutationResult>>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/batch-run-to`,
    {
      method: 'POST',
      body: JSON.stringify(body),
      timeoutMs: BULK_REQUEST_TIMEOUT,
    }
  )
}

export async function batchRerunJobs(
  workspaceId: string,
  nodeKey: string | null,
  target: BatchJobTarget,
  options?: { fromFailedNode?: boolean }
): Promise<BatchOpMaybeAccepted<BatchJobMutationResult>> {
  const body: JobBatchRerunRequest = {
    ...targetBody(target),
    from_failed_node: options?.fromFailedNode ?? false,
  }
  if (nodeKey != null) body.node_key = nodeKey
  return api<BatchOpMaybeAccepted<BatchJobMutationResult>>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/batch-rerun`,
    {
      method: 'POST',
      body: JSON.stringify(body),
      timeoutMs: BULK_REQUEST_TIMEOUT,
    }
  )
}

export async function packageJobs(
  workspaceId: string,
  target: BatchJobTarget
): Promise<BatchOpMaybeAccepted<WorkspacePackageResult>> {
  return api<BatchOpMaybeAccepted<WorkspacePackageResult>>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/package`,
    {
      method: 'POST',
      body: JSON.stringify(targetBody(target)),
      timeoutMs: BULK_REQUEST_TIMEOUT,
    }
  )
}

export async function batchDeleteJobs(
  workspaceId: string,
  target: BatchJobTarget
): Promise<BatchOpMaybeAccepted<BatchJobMutationResult>> {
  return api<BatchOpMaybeAccepted<BatchJobMutationResult>>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/batch`,
    {
      method: 'DELETE',
      body: JSON.stringify(targetBody(target)),
      timeoutMs: BULK_REQUEST_TIMEOUT,
    }
  )
}
