import { api } from './core'
import type { components } from '../generated/api'

export type AgentRegisterTokenSummary =
  components['schemas']['AgentRegisterTokenSummary']
export type AgentRegisterTokenCreatedResponse =
  components['schemas']['AgentRegisterTokenCreatedResponse']
export type AgentWorkerSummary = components['schemas']['AgentWorkerSummary']

type TokensResponse = components['schemas']['AgentRegisterTokensResponse']
type WorkersResponse = components['schemas']['AgentWorkersResponse']
type CreateTokenRequest =
  components['schemas']['CreateAgentRegisterTokenRequest']
type RevokeTokenResponse =
  components['schemas']['AgentRegisterTokenRevokeResponse']
type RevokeWorkerResponse = components['schemas']['AgentWorkerRevokeResponse']

// Management endpoints require an admin session: every route below is gated
// by require_admin on the backend (server/app/routes/agent_workers.py).
export async function listRegisterTokens(): Promise<
  AgentRegisterTokenSummary[]
> {
  const data = await api<TokensResponse>('/api/agent-register-tokens')
  return data.tokens ?? []
}

export async function createRegisterToken(
  input: CreateTokenRequest
): Promise<AgentRegisterTokenCreatedResponse> {
  return api<AgentRegisterTokenCreatedResponse>('/api/agent-register-tokens', {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export async function revokeRegisterToken(
  tokenId: string
): Promise<RevokeTokenResponse> {
  return api<RevokeTokenResponse>(
    `/api/agent-register-tokens/${encodeURIComponent(tokenId)}/revoke`,
    { method: 'POST' }
  )
}

export async function listAgentWorkers(): Promise<AgentWorkerSummary[]> {
  const data = await api<WorkersResponse>('/api/agent-workers')
  return data.workers ?? []
}

export async function revokeAgentWorker(
  workerId: string
): Promise<RevokeWorkerResponse> {
  return api<RevokeWorkerResponse>(
    `/api/agent-workers/${encodeURIComponent(workerId)}/revoke`,
    { method: 'POST' }
  )
}
