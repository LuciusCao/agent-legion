import { api } from './core'
import type { components } from '../generated/api'
import { targetBody, type BatchJobTarget } from './batchTarget'
import type { BatchOpMaybeAccepted } from './jobBatchOperationsApi'
import { BULK_REQUEST_TIMEOUT } from './requestTimeout'

export type ApprovalDecision = components['schemas']['ApprovalDecisionResponse']
export type ApprovalDecisionCreateRequest =
  components['schemas']['ApprovalDecisionCreateRequest']
export type ApprovalVerdict = ApprovalDecisionCreateRequest['verdict']
export type BatchApprovalJobsResult =
  components['schemas']['BatchApprovalJobsResponse']
export type ApprovalBatchKind =
  components['schemas']['ApprovalBatchResultResponse']['operation']

export async function decideApproval(
  workspaceId: string,
  jobId: string,
  nodeKey: string,
  payload: ApprovalDecisionCreateRequest
): Promise<ApprovalDecision> {
  return api<ApprovalDecision>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/${encodeURIComponent(
      jobId
    )}/nodes/${encodeURIComponent(nodeKey)}/approval`,
    { method: 'POST', body: JSON.stringify(payload) }
  )
}

export async function fetchApprovalDecisions(
  workspaceId: string,
  jobId: string
): Promise<ApprovalDecision[]> {
  const response = await api<
    components['schemas']['ApprovalDecisionListResponse']
  >(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/${encodeURIComponent(
      jobId
    )}/approvals`
  )
  return response.decisions
}

export async function batchApprovalJobs(
  workspaceId: string,
  kind: ApprovalBatchKind,
  target: BatchJobTarget
): Promise<BatchOpMaybeAccepted<BatchApprovalJobsResult>> {
  const body: components['schemas']['BatchApprovalJobsRequest'] = {
    ...targetBody(target),
    note: '',
  }
  return api<BatchOpMaybeAccepted<BatchApprovalJobsResult>>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/batch-${kind}`,
    {
      method: 'POST',
      body: JSON.stringify(body),
      timeoutMs: BULK_REQUEST_TIMEOUT,
    }
  )
}
