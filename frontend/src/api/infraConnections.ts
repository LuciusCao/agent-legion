import { api } from './core'
import type { components } from '../generated/api'

export type InfraConnectionsResponse =
  components['schemas']['InfraConnectionsResponse']
export type InfraConnectionTestRequest =
  components['schemas']['InfraConnectionTestRequest']
export type InfraConnectionTestResponse =
  components['schemas']['InfraConnectionTestResponse']
export type InfraConnectionTarget = InfraConnectionTestRequest['target']

const INFRA_CONNECTIONS_URL = '/api/admin/infra-connections'

export async function getInfraConnections(): Promise<InfraConnectionsResponse> {
  return api<InfraConnectionsResponse>(INFRA_CONNECTIONS_URL)
}

export async function testInfraConnection(
  target: InfraConnectionTarget
): Promise<InfraConnectionTestResponse> {
  return api<InfraConnectionTestResponse>(`${INFRA_CONNECTIONS_URL}/test`, {
    method: 'POST',
    body: JSON.stringify({ target }),
  })
}
