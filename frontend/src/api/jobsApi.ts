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

export async function fetchJobDetail(
  jobId: string,
  signal?: AbortSignal
): Promise<JobDetail> {
  return api(`/api/jobs/${encodeURIComponent(jobId)}`, { signal })
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
 *
 * 产物名按**路径段**编码（#1178 codex 复审 P2）：声明产物名可含 /（如
 * reports/final.mp4）。encodeURIComponent 整名编码会把 / 编成 %2F，ASGI
 * 解码后变回路径分隔符——服务端路由按段匹配，名字里的 / 必须保持结构
 * 语义（每段各自编码，段间的 / 原样保留，与 raw 路由的
 * {artifact_name:path} 形态对齐）。
 */
export function jobArtifactRawUrl(jobId: string, artifactName: string): string {
  const encodedName = artifactName.split('/').map(encodeURIComponent).join('/')
  return `/api/jobs/${encodeURIComponent(jobId)}/artifacts/${encodedName}/raw`
}
