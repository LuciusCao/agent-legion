import { api } from './core'
import { wrapDraftConflict } from './workflowDraftConflict'
import type { components } from '../generated/api'

export type WorkflowDraftStoreResponse =
  components['schemas']['WorkflowDraftStoreResponse']
export { WorkflowDraftConflictError } from './workflowDraftConflict'
/* #633：无草稿时的 CAS 基线标记（与后端 DRAFT_NEVER_SAVED 同值）。 */
export const DRAFT_NEVER_SAVED = 'never-saved'

export async function fetchWorkflowDraft(
  workspaceId: string
): Promise<WorkflowDraftStoreResponse> {
  return api(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow-draft`
  )
}

/* #633：expectedUpdatedAt 为 CAS 基线（409 翻译为结构化冲突异常）；
   keepalive 让 pagehide flush 在页面销毁后仍能完成（受 64KB 上限）。 */
export async function putWorkflowDraft(
  workspaceId: string,
  definitionYaml: string,
  options?: { keepalive?: boolean; expectedUpdatedAt?: string | null }
): Promise<WorkflowDraftStoreResponse> {
  const url = `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow-draft`
  const expected = options?.expectedUpdatedAt
  const body = {
    definition_yaml: definitionYaml,
    ...(expected && { expected_updated_at: expected }),
  }
  return wrapDraftConflict(
    api(url, {
      method: 'PUT',
      body: JSON.stringify(body),
      ...(options?.keepalive && { keepalive: true }),
    })
  )
}
