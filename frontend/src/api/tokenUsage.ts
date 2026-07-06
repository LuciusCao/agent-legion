import { api } from './core'
import type { components } from '../generated/api'

export type TokenUsageWorkspaceResponse =
  components['schemas']['TokenUsageWorkspaceResponse']

export type TokenUsageWorkspaceGroup =
  components['schemas']['TokenUsageWorkspaceGroup']

export async function fetchWorkspaceTokenUsage(
  workspaceId: string,
  params: URLSearchParams = new URLSearchParams()
): Promise<TokenUsageWorkspaceResponse> {
  const query = params.toString()
  return api<TokenUsageWorkspaceResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/token-usage${query ? `?${query}` : ''}`
  )
}
