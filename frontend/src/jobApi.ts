import { api } from './api'
import type { components } from './generated/api'

export type JobLogResponse = components['schemas']['JobLogResponse']

export async function fetchJobLog(
  jobId: string,
  runId: number
): Promise<JobLogResponse> {
  return api<JobLogResponse>(
    `/api/jobs/${encodeURIComponent(jobId)}/runs/${encodeURIComponent(runId)}/log`
  )
}
