import type { components } from './generated/api'

export type TokenUsageRunResponse =
  components['schemas']['TokenUsageRunResponse']
export type TokenUsageJobResponse =
  components['schemas']['TokenUsageJobResponse']
export type TokenUsageWorkspaceResponse =
  components['schemas']['TokenUsageWorkspaceResponse']

export interface RunUsageCost {
  input: number
  output: number
  cache_read: number
  total: number
  currency: string
  pricing_missing: boolean
}

export interface RunUsage {
  node_run_id: number
  node_key: string
  provider: string
  model: string
  skill_version: string
  message_count: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  total_tokens: number
  cost: RunUsageCost
  is_complete: boolean
  usage_source: string
}
