import { api } from './api'
import type { components } from './generated/api'

export type VideoJobDetailResponse =
  components['schemas']['VideoJobDetailResponse']

export async function fetchJobVideoDetail(
  jobId: string
): Promise<VideoJobDetailResponse> {
  return api<VideoJobDetailResponse>(
    `/api/jobs/${encodeURIComponent(jobId)}/video`
  )
}
