import type { TokenUsagePricingRate } from '../../api/tokenUsagePricing'
import type { RateRow } from './ModelPricingSection'

// 模型定价行的序列化/反序列化（从 GlobalSettingsPage 拆出，体积预算约束）。
export function toRows(pricing: TokenUsagePricingRate[]): RateRow[] {
  return pricing.map((rate) => ({
    provider: rate.provider,
    model: rate.model,
    input_per_1m: String(rate.input_per_1m),
    output_per_1m: String(rate.output_per_1m),
    cache_read_per_1m: String(rate.cache_read_per_1m),
  }))
}

export function serialize(currency: string, rows: RateRow[]): string {
  return JSON.stringify({
    currency: currency.trim(),
    pricing: rows.map((row) => ({
      provider: row.provider.trim(),
      model: row.model.trim(),
      input_per_1m: Number(row.input_per_1m),
      output_per_1m: Number(row.output_per_1m),
      cache_read_per_1m: Number(row.cache_read_per_1m),
    })),
  })
}
