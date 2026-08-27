import { api } from './core'
import type { components } from '../generated/api'

export type AgentRegisterTokenSummary =
  components['schemas']['AgentRegisterTokenSummary']
export type AgentRegisterTokenCreatedResponse =
  components['schemas']['AgentRegisterTokenCreatedResponse']

type TokensResponse = components['schemas']['AgentRegisterTokensResponse']
type CreateTokenRequest =
  components['schemas']['CreateAgentRegisterTokenRequest']
type DeleteTokenResponse =
  components['schemas']['AgentRegisterTokenDeleteResponse']

// Management endpoints require an admin session: every route below is gated
// by require_admin on the backend (server/app/routes/agent_register_tokens.py).
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

// Hard-delete is the only lifecycle action on a key: it stops resolving
// immediately, so Workers holding it fail their next re-registration.
export async function deleteRegisterToken(
  tokenId: string
): Promise<DeleteTokenResponse> {
  return api<DeleteTokenResponse>(
    `/api/agent-register-tokens/${encodeURIComponent(tokenId)}`,
    { method: 'DELETE' }
  )
}
