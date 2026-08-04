import { api } from './core'
import type { JobDetail } from '../types/jobTypes'
import type {
  ArtifactResponse,
  CreateJobBatchInput,
  JobBatchResponse,
  JobsResponse,
} from '../types'

export async function fetchJobs(
  workspaceId: string,
  workflowKey?: string
): Promise<JobsResponse> {
  const params = new URLSearchParams()
  if (workflowKey) params.set('workflow_key', workflowKey)
  const query = params.toString()
  return api(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs${query ? `?${query}` : ''}`
  )
}

export async function createJobBatch(
  workspaceId: string,
  input: CreateJobBatchInput
): Promise<JobBatchResponse> {
  return api(`/api/workspaces/${encodeURIComponent(workspaceId)}/job-batches`, {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export async function fetchJobDetail(jobId: string): Promise<JobDetail> {
  return api(`/api/jobs/${encodeURIComponent(jobId)}`)
}

export async function deleteJob(jobId: string): Promise<{ deleted: string }> {
  return api(`/api/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' })
}

export async function fetchJobArtifact(
  jobId: string,
  artifactName: string
): Promise<ArtifactResponse> {
  return api(
    `/api/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(artifactName)}`
  )
}
