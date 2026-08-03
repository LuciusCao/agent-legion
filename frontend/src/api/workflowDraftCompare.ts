import { api } from './core'
import type { components } from '../generated/api'

export async function compareWorkflowDraft(
  workspaceId: string,
  request: components['schemas']['WorkflowDraftCompareRequest']
): Promise<components['schemas']['WorkflowDraftCompareResponse']> {
  return api(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow-drafts/compare`,
    { method: 'POST', body: JSON.stringify(request) }
  )
}
