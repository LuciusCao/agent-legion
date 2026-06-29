import { api } from './api'
import type { components } from './generated/api'

export type JobLogResponse = components['schemas']['JobLogResponse']
export type JobMutationResult =
  components['schemas']['JobMutationResultResponse']
export type BatchJobMutationResult =
  components['schemas']['BatchJobMutationResponse']
export type WorkspacePackageResult =
  components['schemas']['WorkspacePackageResponse']
export type RunToRequest =
  components['schemas']['server__app__routes__job_operation_contracts__RunToRequest']
export type ContinueJobRequest = components['schemas']['ContinueJobRequest']
export type BatchRunToRequest =
  components['schemas']['server__app__routes__job_operation_contracts__BatchRunToRequest']

export async function fetchJobLog(
  jobId: string,
  runId: number
): Promise<JobLogResponse> {
  return api<JobLogResponse>(
    `/api/jobs/${encodeURIComponent(jobId)}/runs/${encodeURIComponent(runId)}/log`
  )
}

export async function rerunJob(
  jobId: string,
  nodeKey: string
): Promise<JobMutationResult> {
  return api<JobMutationResult>(
    `/api/jobs/${encodeURIComponent(jobId)}/nodes/${encodeURIComponent(nodeKey)}/rerun`,
    { method: 'POST' }
  )
}

export async function runToJob(
  jobId: string,
  targetNodeKey: string,
  startNodeKey?: string | null
): Promise<JobMutationResult> {
  const body: Record<string, unknown> = { target_node_key: targetNodeKey }
  if (startNodeKey != null) {
    body.start_node_key = startNodeKey
  }
  return api<JobMutationResult>(
    `/api/jobs/${encodeURIComponent(jobId)}/run-to`,
    {
      method: 'POST',
      body: JSON.stringify(body),
    }
  )
}

export async function continueJob(jobId: string): Promise<JobMutationResult> {
  return api<JobMutationResult>(
    `/api/jobs/${encodeURIComponent(jobId)}/continue`,
    { method: 'POST' }
  )
}

export async function batchRunToJobs(
  workspaceId: string,
  targetNodeKey: string,
  jobIds: string[],
  startNodeKey?: string | null
): Promise<BatchJobMutationResult> {
  const body: Record<string, unknown> = {
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
  const body: Record<string, unknown> = { job_ids: jobIds }
  if (nodeKey != null) {
    body.node_key = nodeKey
  }
  if (options?.fromFailedNode) {
    body.from_failed_node = true
  }
  return api<BatchJobMutationResult>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/batch-rerun`,
    {
      method: 'POST',
      body: JSON.stringify(body),
    }
  )
}

export async function packageJobs(
  workspaceId: string,
  jobIds: string[]
): Promise<WorkspacePackageResult> {
  return api<WorkspacePackageResult>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/package`,
    {
      method: 'POST',
      body: JSON.stringify({ job_ids: jobIds }),
    }
  )
}

export async function batchDeleteJobs(
  workspaceId: string,
  jobIds: string[]
): Promise<BatchJobMutationResult> {
  return api<BatchJobMutationResult>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/batch`,
    {
      method: 'DELETE',
      body: JSON.stringify({ job_ids: jobIds }),
    }
  )
}
