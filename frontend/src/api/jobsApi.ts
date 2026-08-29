import { api } from './core'
import type { JobDetail } from '../types/jobTypes'
import type {
  ArtifactResponse,
  CreateJobBatchInput,
  JobBatchResponse,
} from '../types'

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

/**
 * raw 字节端点的同源 URL：媒体渲染器 <img>/<video>/<audio>/<iframe> 直接
 * 作 src 用（session cookie 自动携带，GET 免 CSRF）。不经 api() fetch——
 * 返回的是二进制流而非 JSON。
 */
export function jobArtifactRawUrl(jobId: string, artifactName: string): string {
  return `/api/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(artifactName)}/raw`
}
