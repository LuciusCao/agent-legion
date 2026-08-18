import { api } from './core'
import type { components } from '../generated/api'

export type StudioAgentRegistryResponse =
  components['schemas']['StudioAgentRegistryResponse']
export type StudioAgentRegistryUpdate =
  components['schemas']['StudioAgentRegistryUpdate']
export type StudioAgentRegistryEntry =
  components['schemas']['StudioAgentRegistryEntry']

const STUDIO_AGENTS_URL = '/api/admin/studio-agents'

export async function getStudioAgents(): Promise<StudioAgentRegistryResponse> {
  return api<StudioAgentRegistryResponse>(STUDIO_AGENTS_URL)
}

export async function updateStudioAgents(
  input: StudioAgentRegistryUpdate
): Promise<StudioAgentRegistryResponse> {
  return api<StudioAgentRegistryResponse>(STUDIO_AGENTS_URL, {
    method: 'PUT',
    body: JSON.stringify(input),
  })
}
