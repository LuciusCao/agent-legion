import { api } from './core'
import type { components } from '../generated/api'

export type WorkspaceApiTokenSummary =
  components['schemas']['WorkspaceApiTokenSummary']
export type WorkspaceApiTokenCreatedResponse =
  components['schemas']['WorkspaceApiTokenCreatedResponse']

type TokensResponse = components['schemas']['WorkspaceApiTokensResponse']
type CreateTokenRequest =
  components['schemas']['CreateWorkspaceApiTokenRequest']
type RevokeTokenResponse =
  components['schemas']['WorkspaceApiTokenRevokeResponse']

// Management endpoints are admin-only on the backend
// (server/app/routes/workspace_api_tokens.py); the workspace scope comes
// from the path, so the panel never picks a foreign workspace.
export async function listWorkspaceApiTokens(
  workspaceId: string
): Promise<WorkspaceApiTokenSummary[]> {
  const data = await api<TokensResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/api-tokens`
  )
  return data.tokens ?? []
}

export async function createWorkspaceApiToken(
  workspaceId: string,
  input: CreateTokenRequest
): Promise<WorkspaceApiTokenCreatedResponse> {
  return api<WorkspaceApiTokenCreatedResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/api-tokens`,
    {
      method: 'POST',
      body: JSON.stringify(input),
    }
  )
}

// Soft revoke: the credential stops resolving immediately; the row (and its
// last_used_at watermark) stays for the audit list.
export async function revokeWorkspaceApiToken(
  workspaceId: string,
  tokenId: string
): Promise<RevokeTokenResponse> {
  return api<RevokeTokenResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/api-tokens/${encodeURIComponent(tokenId)}`,
    { method: 'DELETE' }
  )
}
