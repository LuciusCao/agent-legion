import { api } from './core'
import type { components } from '../generated/api'

export type InstanceSettingsResponse =
  components['schemas']['InstanceSettingsResponse']
export type InstanceSettingsUpdate =
  components['schemas']['InstanceSettingsUpdate']

const INSTANCE_SETTINGS_URL = '/api/admin/instance-settings'

export async function getInstanceSettings(): Promise<InstanceSettingsResponse> {
  return api<InstanceSettingsResponse>(INSTANCE_SETTINGS_URL)
}

export async function updateInstanceSettings(
  input: InstanceSettingsUpdate
): Promise<InstanceSettingsResponse> {
  return api<InstanceSettingsResponse>(INSTANCE_SETTINGS_URL, {
    method: 'PUT',
    body: JSON.stringify(input),
  })
}
