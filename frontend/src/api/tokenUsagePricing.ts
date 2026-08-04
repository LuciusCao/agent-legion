import { api } from './core'
import type { components } from '../generated/api'

export type TokenUsagePricingRate =
  components['schemas']['TokenUsagePricingRate']
export type TokenUsagePricingConfigResponse =
  components['schemas']['TokenUsagePricingConfigResponse']
export type TokenUsagePricingConfigUpdate =
  components['schemas']['TokenUsagePricingConfigUpdate']

const PRICING_URL = '/api/admin/token-usage-pricing'

export async function getTokenUsagePricing(): Promise<TokenUsagePricingConfigResponse> {
  return api<TokenUsagePricingConfigResponse>(PRICING_URL)
}

export async function updateTokenUsagePricing(
  input: TokenUsagePricingConfigUpdate
): Promise<TokenUsagePricingConfigResponse> {
  return api<TokenUsagePricingConfigResponse>(PRICING_URL, {
    method: 'PUT',
    body: JSON.stringify(input),
  })
}
