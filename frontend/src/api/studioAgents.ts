import { api } from './core'
import type { components } from '../generated/api'

export type StudioAgentDetection = components['schemas']['StudioAgentDetection']
export type StudioAgentRegistryEntry =
  components['schemas']['StudioAgentRegistryEntry']
export type StudioAgentRegistryResponse =
  components['schemas']['StudioAgentRegistryResponse']
export type StudioAgentRegistryUpdate =
  components['schemas']['StudioAgentRegistryUpdate']

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

export async function redetectStudioAgents() {
  return api<StudioAgentRegistryResponse>(`${STUDIO_AGENTS_URL}/redetect`, {
    method: 'POST',
  })
}
