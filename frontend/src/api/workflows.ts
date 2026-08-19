import { api } from './core'
import type { WorkflowDraftValidationResponse } from '../types'

export async function validateWorkflowDraft(
  workspaceId: string,
  definitionYaml: string
): Promise<WorkflowDraftValidationResponse> {
  return api<WorkflowDraftValidationResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow-drafts/validate`,
    {
      method: 'POST',
      body: JSON.stringify({ definition_yaml: definitionYaml }),
    }
  )
}

export async function publishWorkflowDraft(
  workspaceId: string,
  definitionYaml: string
): Promise<WorkflowDraftValidationResponse> {
  return api<WorkflowDraftValidationResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow-drafts/publish`,
    {
      method: 'POST',
      body: JSON.stringify({ definition_yaml: definitionYaml }),
    }
  )
}
