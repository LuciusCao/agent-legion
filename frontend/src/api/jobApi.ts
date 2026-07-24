import { api } from './core'
import type {
  ContinueJobRequest,
  JobLogResponse,
  JobMutationResult,
  RunToRequest,
} from '../types/jobTypes'
import type {
  TokenUsageJobResponse,
  TokenUsageRunResponse,
} from '../types/tokenUsageTypes'

export async function fetchJobLog(
  jobId: string,
  runId: number
): Promise<JobLogResponse> {
  return api<JobLogResponse>(
    `/api/jobs/${encodeURIComponent(jobId)}/runs/${encodeURIComponent(runId)}/log`
  )
}

export async function fetchRunTokenUsage(
  jobId: string,
  runId: number
): Promise<TokenUsageRunResponse> {
  return api<TokenUsageRunResponse>(
    `/api/jobs/${encodeURIComponent(jobId)}/runs/${encodeURIComponent(runId)}/token-usage`
  )
}

export async function fetchJobTokenUsage(
  jobId: string
): Promise<TokenUsageJobResponse> {
  return api<TokenUsageJobResponse>(
    `/api/jobs/${encodeURIComponent(jobId)}/token-usage`
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
  const body: RunToRequest = { target_node_key: targetNodeKey }
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
  const body: ContinueJobRequest = {}
  return api<JobMutationResult>(
    `/api/jobs/${encodeURIComponent(jobId)}/continue`,
    { method: 'POST', body: JSON.stringify(body) }
  )
}

export {
  batchDeleteJobs,
  batchRerunJobs,
  batchRunToJobs,
  clearJobsPackedStatus,
  packageJobs,
} from './jobBatchApi'
