import { api } from './core'
import type {
  AgentCreateRequest,
  AgentDefinitionPayload,
  AgentDetailResponse,
  AgentListResponse,
  AgentVersion,
  AgentVersionsResponse,
} from '../types'

const base = '/api/agent-definitions'

export async function fetchAgentDefinitions(): Promise<AgentListResponse> {
  return api(base)
}

export async function fetchAgentDefinition(
  agentId: string
): Promise<AgentDetailResponse> {
  return api(`${base}/${encodeURIComponent(agentId)}`)
}

export async function fetchAgentVersions(
  agentId: string
): Promise<AgentVersionsResponse> {
  return api(`${base}/${encodeURIComponent(agentId)}/versions`)
}

export async function createAgentDefinition(
  payload: AgentCreateRequest
): Promise<AgentVersion> {
  return api(base, { method: 'POST', body: JSON.stringify(payload) })
}

export async function saveAgentDraft(
  agentId: string,
  payload: AgentDefinitionPayload
): Promise<AgentVersion> {
  return api(`${base}/${encodeURIComponent(agentId)}/draft`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export async function publishAgent(agentId: string): Promise<AgentVersion> {
  return api(`${base}/${encodeURIComponent(agentId)}/publish`, {
    method: 'POST',
  })
}

export async function rollbackAgent(
  agentId: string,
  version: number
): Promise<AgentVersion> {
  return api(`${base}/${encodeURIComponent(agentId)}/rollback`, {
    method: 'POST',
    body: JSON.stringify({ version }),
  })
}

export async function copyAgent(
  agentId: string,
  newAgentId: string
): Promise<AgentVersion> {
  return api(`${base}/${encodeURIComponent(agentId)}/copy`, {
    method: 'POST',
    body: JSON.stringify({ new_agent_id: newAgentId }),
  })
}

export async function archiveAgent(
  agentId: string
): Promise<{ archived: number }> {
  return api(`${base}/${encodeURIComponent(agentId)}`, { method: 'DELETE' })
}
