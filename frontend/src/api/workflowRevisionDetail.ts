import { api } from './core'
import type { WorkflowRevisionDetailResponse } from '../types'

export async function fetchWorkflowRevisionDetail(
  workspaceId: string,
  revisionId: string
): Promise<WorkflowRevisionDetailResponse> {
  return api(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow-revisions/${encodeURIComponent(revisionId)}`
  )
}
