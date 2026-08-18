import { api } from './core'
import type {
  AgentDefaults,
  WorkspaceRecord,
  WorkspaceResponse,
  WorkspaceSettingsResponse,
  WorkspacesResponse,
} from '../types'
import type { WorkspaceStats } from '../types/workspaceTypes'

export async function fetchWorkspaces(): Promise<WorkspacesResponse> {
  return api('/api/workspaces')
}

export async function createWorkspace(
  name: string,
  workflowKey: string,
  resourceConfig: Record<string, unknown> = {},
  defaultEntity: string = 'question',
  intakeConfig: Record<string, unknown> = {},
  workflowMode: 'demo' | 'blank' = 'demo'
): Promise<WorkspaceRecord> {
  const result = await api<WorkspaceResponse>('/api/workspaces', {
    method: 'POST',
    body: JSON.stringify({
      name,
      default_workflow_key: workflowKey,
      resource_config: resourceConfig,
      default_entity: defaultEntity,
      intake_config: intakeConfig,
      workflow_mode: workflowMode,
    }),
  })
  return result.workspace
}

export async function updateWorkspace(
  workspaceId: string,
  fields: {
    name?: string
    description?: string
    default_workflow_key?: string
    default_entity?: string
    resource_config?: Record<string, unknown>
    intake_config?: Record<string, unknown>
  }
): Promise<WorkspaceRecord> {
  const result = await api<WorkspaceResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}`,
    {
      method: 'PATCH',
      body: JSON.stringify(fields),
    }
  )
  return result.workspace
}

export async function fetchWorkspaceStats(
  workspaceId: string
): Promise<WorkspaceStats> {
  return api(`/api/workspaces/${encodeURIComponent(workspaceId)}/stats`)
}

export async function deleteWorkspace(workspaceId: string): Promise<void> {
  await api(`/api/workspaces/${encodeURIComponent(workspaceId)}`, {
    method: 'DELETE',
  })
}

export async function updateAgentDefaults(
  workspaceId: string,
  agentDefaults: AgentDefaults
): Promise<WorkspaceSettingsResponse> {
  return api(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/settings/agent-defaults`,
    {
      method: 'PATCH',
      body: JSON.stringify({ agentDefaults }),
    }
  )
}
