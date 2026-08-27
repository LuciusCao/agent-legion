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
  definitionYaml: string
): Promise<WorkflowDraftStoreResponse> {
  return api<WorkflowDraftStoreResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow-draft`,
    {
      method: 'PUT',
      body: JSON.stringify({ definition_yaml: definitionYaml }),
    }
  )
}
