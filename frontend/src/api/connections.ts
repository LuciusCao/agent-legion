import { api } from './core'
import type { components } from '../generated/api'

export type ConnectionView = components['schemas']['ConnectionView']
export type ConnectionCreate = components['schemas']['ConnectionCreate']
export type ConnectionUpdate = components['schemas']['ConnectionUpdate']
export type ConnectionListResponse =
  components['schemas']['ConnectionListResponse']
export type ConnectionTestResponse =
  components['schemas']['ConnectionTestResponse']
export type ConnectionTypeView = components['schemas']['ConnectionTypeView']
export type ConnectionTypesResponse =
  components['schemas']['ConnectionTypesResponse']

const CONNECTIONS_URL = '/api/admin/connections'
const CONNECTION_TYPES_URL = '/api/admin/connection-types'

export async function getConnections(): Promise<ConnectionListResponse> {
  return api<ConnectionListResponse>(CONNECTIONS_URL)
}

export async function getConnectionTypes(): Promise<ConnectionTypesResponse> {
  return api<ConnectionTypesResponse>(CONNECTION_TYPES_URL)
}

export async function createConnection(
  input: ConnectionCreate
): Promise<ConnectionView> {
  return api<ConnectionView>(CONNECTIONS_URL, {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export async function updateConnection(
  key: string,
  input: ConnectionUpdate
): Promise<ConnectionView> {
  return api<ConnectionView>(`${CONNECTIONS_URL}/${encodeURIComponent(key)}`, {
    method: 'PUT',
    body: JSON.stringify(input),
  })
}

export async function deleteConnection(
  key: string
): Promise<ConnectionTestResponse> {
  return api<ConnectionTestResponse>(
    `${CONNECTIONS_URL}/${encodeURIComponent(key)}`,
    { method: 'DELETE' }
  )
}

export async function testConnection(
  key: string
): Promise<ConnectionTestResponse> {
  return api<ConnectionTestResponse>(
    `${CONNECTIONS_URL}/${encodeURIComponent(key)}/test`,
    { method: 'POST' }
  )
}
