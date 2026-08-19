import { api } from './core'
import { targetBody, type BatchJobTarget } from './batchTarget'
import type { components } from '../generated/api'
import type { BatchJobMutationResult } from '../types/jobTypes'

type BatchPauseJobsRequest = components['schemas']['BatchPauseJobsRequest']
type BatchResumeJobsRequest = components['schemas']['BatchResumeJobsRequest']

export async function batchPauseJobs(
  workspaceId: string,
  target: BatchJobTarget,
  reason?: string
): Promise<BatchJobMutationResult> {
  const body: BatchPauseJobsRequest = { ...targetBody(target) }
  if (reason != null && reason.trim() !== '') body.reason = reason.trim()
  return api<BatchJobMutationResult>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/batch-pause`,
    { method: 'POST', body: JSON.stringify(body) }
  )
}

export async function batchResumeJobs(
  workspaceId: string,
  target: BatchJobTarget
): Promise<BatchJobMutationResult> {
  const body: BatchResumeJobsRequest = { ...targetBody(target) }
  return api<BatchJobMutationResult>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/batch-resume`,
    { method: 'POST', body: JSON.stringify(body) }
  )
}
