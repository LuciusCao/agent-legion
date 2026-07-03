import { api } from './core'
import type {
  ActiveWorkflowRevisionResponse,
  WorkflowRevisionsResponse,
} from '../types'

export { compareWorkflowDraft } from './workflow_draft_compare'

export async function fetchActiveWorkflowRevision(
  workspaceId: string
): Promise<ActiveWorkflowRevisionResponse> {
  return api(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow-revisions/active`
  )
}

export async function fetchWorkflowRevisions(
  workspaceId: string
): Promise<WorkflowRevisionsResponse> {
  return api(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow-revisions`
  )
}
