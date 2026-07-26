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

const MANAGEMENT_HEADER = 'X-Agent-Worker-Register-Token'

function managementInit(
  managementToken: string,
  init?: RequestInit
): RequestInit {
  return {
    ...init,
    headers: { [MANAGEMENT_HEADER]: managementToken, ...(init?.headers ?? {}) },
  }
}

/** True when the backend rejected the management token (401). */
export function isManagementAuthError(error: unknown): boolean {
  return (
    error instanceof Error &&
    (error as Error & { status?: number }).status === 401
  )
}

export async function listRegisterTokens(
  managementToken: string
): Promise<AgentRegisterTokenSummary[]> {
  const data = await api<TokensResponse>(
    '/api/agent-register-tokens',
    managementInit(managementToken)
  )
  return data.tokens ?? []
}

export async function createRegisterToken(
  managementToken: string,
  input: CreateTokenRequest
): Promise<AgentRegisterTokenCreatedResponse> {
  return api<AgentRegisterTokenCreatedResponse>(
    '/api/agent-register-tokens',
    managementInit(managementToken, {
      method: 'POST',
      body: JSON.stringify(input),
    })
  )
}

export async function revokeRegisterToken(
  managementToken: string,
  tokenId: string
): Promise<RevokeTokenResponse> {
  return api<RevokeTokenResponse>(
    `/api/agent-register-tokens/${encodeURIComponent(tokenId)}/revoke`,
    managementInit(managementToken, { method: 'POST' })
  )
}

export async function listAgentWorkers(): Promise<AgentWorkerSummary[]> {
  const data = await api<WorkersResponse>('/api/agent-workers')
  return data.workers ?? []
}

export async function revokeAgentWorker(
  managementToken: string,
  workerId: string
): Promise<RevokeWorkerResponse> {
  return api<RevokeWorkerResponse>(
    `/api/agent-workers/${encodeURIComponent(workerId)}/revoke`,
    managementInit(managementToken, { method: 'POST' })
  )
}
