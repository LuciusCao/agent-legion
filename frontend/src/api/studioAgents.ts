import { api } from './core'
import type { components } from '../generated/api'

// #332 过渡桥：source/detection 是新契约字段，但 generated/api.ts 的再生成
// 归另一 mission；重新生成后这里应改回纯 components[...] 派生并删除本注释。
export interface StudioAgentDetection {
  detected: boolean
  path?: string | null
  version?: string | null
}
export type StudioAgentRegistryResponse =
  components['schemas']['StudioAgentRegistryResponse'] & {
    agents?: (components['schemas']['StudioAgentRegistryEntry'] & {
      source?: 'manual' | 'detected'
    })[]
    detection?: Record<string, StudioAgentDetection>
  }
export type StudioAgentRegistryUpdate =
  components['schemas']['StudioAgentRegistryUpdate']

// 三个端点共享同一 base URL：/api/admin/studio-agents[/redetect]。
export async function getStudioAgents(): Promise<StudioAgentRegistryResponse> {
  return api<StudioAgentRegistryResponse>('/api/admin/studio-agents')
}

export async function updateStudioAgents(input: StudioAgentRegistryUpdate) {
  return api<StudioAgentRegistryResponse>('/api/admin/studio-agents', {
    method: 'PUT',
    body: JSON.stringify(input),
  })
}

export async function redetectStudioAgents() {
  return api<StudioAgentRegistryResponse>('/api/admin/studio-agents/redetect', {
    method: 'POST',
  })
}
