import { api } from './core'
import { BULK_REQUEST_TIMEOUT } from './requestTimeout'
import { targetBody, type BatchJobTarget } from './batchTarget'
import type { BatchOpMaybeAccepted } from './jobBatchOperationsApi'
import type { components } from '../generated/api'
import type { BatchJobMutationResult } from '../types/jobTypes'

type BatchPauseJobsRequest = components['schemas']['BatchPauseJobsRequest']
type BatchResumeJobsRequest = components['schemas']['BatchResumeJobsRequest']

export async function batchPauseJobs(
  workspaceId: string,
  target: BatchJobTarget,
  reason?: string
): Promise<BatchOpMaybeAccepted<BatchJobMutationResult>> {
  const body: BatchPauseJobsRequest = { ...targetBody(target) }
  if (reason != null && reason.trim() !== '') body.reason = reason.trim()
  return api<BatchOpMaybeAccepted<BatchJobMutationResult>>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/batch-pause`,
    {
      method: 'POST',
      body: JSON.stringify(body),
      timeoutMs: BULK_REQUEST_TIMEOUT,
    }
  )
}

export async function batchResumeJobs(
  workspaceId: string,
  target: BatchJobTarget
): Promise<BatchOpMaybeAccepted<BatchJobMutationResult>> {
  const body: BatchResumeJobsRequest = { ...targetBody(target) }
  return api<BatchOpMaybeAccepted<BatchJobMutationResult>>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/batch-resume`,
    {
      method: 'POST',
      body: JSON.stringify(body),
      timeoutMs: BULK_REQUEST_TIMEOUT,
    }
  )
}
