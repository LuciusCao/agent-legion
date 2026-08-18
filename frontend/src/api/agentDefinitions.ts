import { api } from './core'
import type {
  AgentCreateRequest,
  AgentDefinitionPayload,
  AgentDetailResponse,
  AgentListResponse,
  AgentVersion,
  AgentVersionsResponse,
} from '../types'

// Agent 定义目录是 workspace 作用域（schema v46）：所有端点都带
// workspace_id 查询参数，后端同时用它做成员校验。
const base = '/api/agent-definitions'

function scoped(path: string, workspaceId: string): string {
  return `${path}?workspace_id=${encodeURIComponent(workspaceId)}`
}

function item(agentId: string): string {
  return `${base}/${encodeURIComponent(agentId)}`
}

export const fetchAgentDefinitions = (workspaceId: string) =>
  api<AgentListResponse>(scoped(base, workspaceId))

export const fetchAgentDefinition = (workspaceId: string, agentId: string) =>
  api<AgentDetailResponse>(scoped(item(agentId), workspaceId))

export const fetchAgentVersions = (workspaceId: string, agentId: string) =>
  api<AgentVersionsResponse>(scoped(`${item(agentId)}/versions`, workspaceId))

export const createAgentDefinition = (
  workspaceId: string,
  payload: AgentCreateRequest
) =>
  api<AgentVersion>(scoped(base, workspaceId), {
    method: 'POST',
    body: JSON.stringify(payload),
  })

export const saveAgentDraft = (
  workspaceId: string,
  agentId: string,
  payload: AgentDefinitionPayload
) =>
  api<AgentVersion>(scoped(`${item(agentId)}/draft`, workspaceId), {
    method: 'PUT',
    body: JSON.stringify(payload),
  })

export const publishAgent = (workspaceId: string, agentId: string) =>
  api<AgentVersion>(scoped(`${item(agentId)}/publish`, workspaceId), {
    method: 'POST',
  })

export const rollbackAgent = (
  workspaceId: string,
  agentId: string,
  version: number
) =>
  api<AgentVersion>(scoped(`${item(agentId)}/rollback`, workspaceId), {
    method: 'POST',
    body: JSON.stringify({ version }),
  })

export const copyAgent = (
  workspaceId: string,
  agentId: string,
  newAgentId: string
) =>
  api<AgentVersion>(scoped(`${item(agentId)}/copy`, workspaceId), {
    method: 'POST',
    body: JSON.stringify({ new_agent_id: newAgentId }),
  })

export const archiveAgent = (workspaceId: string, agentId: string) =>
  api<{ archived: number }>(scoped(item(agentId), workspaceId), {
    method: 'DELETE',
  })
