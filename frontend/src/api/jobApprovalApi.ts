import { api } from './core'
import type { components } from '../generated/api'

export type ApprovalDecision = components['schemas']['ApprovalDecisionResponse']
export type ApprovalDecisionCreateRequest =
  components['schemas']['ApprovalDecisionCreateRequest']
export type ApprovalVerdict = ApprovalDecisionCreateRequest['verdict']

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
