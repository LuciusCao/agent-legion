import type { components } from './generated/api'

export type TokenUsageRunResponse =
  components['schemas']['TokenUsageRunResponse']
export type TokenUsageJobResponse =
  components['schemas']['TokenUsageJobResponse']
export type TokenUsageWorkspaceResponse =
  components['schemas']['TokenUsageWorkspaceResponse']

export type RunUsage = NonNullable<TokenUsageRunResponse['usage']>
export type RunUsageCost = RunUsage['cost']
