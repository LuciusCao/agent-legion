import { api } from './core'
import type { WorkflowResponse, WorkflowsListResponse } from '../types'

export async function fetchWorkflows(): Promise<WorkflowsListResponse> {
  return api('/api/workflows')
}

export async function fetchWorkflowDefinition(
  workflowKey: string
): Promise<WorkflowResponse> {
  return api(`/api/workflows/${encodeURIComponent(workflowKey)}`)
}

export async function validateWorkflowDraft(
  workspaceId: string,
  definitionYaml: string
): Promise<{ valid: boolean; errors: string[] }> {
  return api<{ valid: boolean; errors: string[] }>(
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
): Promise<{ valid: boolean; errors: string[] }> {
  return api<{ valid: boolean; errors: string[] }>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow-drafts/publish`,
    {
      method: 'POST',
      body: JSON.stringify({ definition_yaml: definitionYaml }),
    }
  )
}
