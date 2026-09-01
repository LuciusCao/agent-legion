import { api } from './core'
import type { components } from '../generated/api'

export type WorkflowDraftStoreResponse =
  components['schemas']['WorkflowDraftStoreResponse']

export async function fetchWorkflowDraft(
  workspaceId: string
): Promise<WorkflowDraftStoreResponse> {
  return api(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow-draft`
  )
}

export async function putWorkflowDraft(
  workspaceId: string,
  definitionYaml: string,
  options?: { keepalive?: boolean }
): Promise<WorkflowDraftStoreResponse> {
  return api<WorkflowDraftStoreResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow-draft`,
    {
      method: 'PUT',
      body: JSON.stringify({ definition_yaml: definitionYaml }),
      // pagehide flush：keepalive 让请求在页面销毁后仍能完成（受 64KB 上限，
      // 调用方按体量决定是否启用）。
      ...(options?.keepalive ? { keepalive: true } : {}),
    }
  )
}
