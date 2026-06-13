import { api } from './api'
import type { components } from './generated/api'

export type JobLogResponse = components['schemas']['JobLogResponse']
export type JobMutationResult =
  components['schemas']['JobMutationResultResponse']
export type BatchJobMutationResult =
  components['schemas']['BatchJobMutationResponse']
export type WorkspacePackageResult =
  components['schemas']['WorkspacePackageResponse']

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

export async function batchRerunJobs(
  workspaceId: string,
  nodeKey: string,
  jobIds: string[]
): Promise<BatchJobMutationResult> {
  return api<BatchJobMutationResult>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/batch-rerun`,
    {
      method: 'POST',
      body: JSON.stringify({ job_ids: jobIds, node_key: nodeKey }),
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
