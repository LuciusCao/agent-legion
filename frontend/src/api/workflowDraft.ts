import { api } from './core'
import { wrapDraftConflict } from './workflowDraftConflict'
import type { components } from '../generated/api'

export type WorkflowDraftStoreResponse =
  components['schemas']['WorkflowDraftStoreResponse']
export { WorkflowDraftConflictError } from './workflowDraftConflict'

export async function fetchWorkflowDraft(
  workspaceId: string
): Promise<WorkflowDraftStoreResponse> {
  return api(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow-draft`
  )
}

/* #633 + #1226 五轮收口：baseGeneration 为 CAS 基线（草稿代次，落后即
   409）；keepalive 让 pagehide flush 在页面销毁后仍能完成（受 64KB 上限）。
   缺省 baseGeneration = legacy 无代次 LWW（兼容残余）。 */
export async function putWorkflowDraft(
  workspaceId: string,
  definitionYaml: string,
  options?: { keepalive?: boolean; baseGeneration?: number | null }
): Promise<WorkflowDraftStoreResponse> {
  const url = `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow-draft`
  const body = {
    definition_yaml: definitionYaml,
    ...(options?.baseGeneration != null && {
      base_generation: options.baseGeneration,
    }),
  }
  return wrapDraftConflict(
    api(url, {
      method: 'PUT',
      body: JSON.stringify(body),
      ...(options?.keepalive && { keepalive: true }),
    })
  )
}
