import type {
  CampaignRecord,
  CampaignWatermarkSample,
} from '../../types/campaignTypes'

/**
 * Campaign progress_json 的读取辅助（纯函数，供列表/详情与测试共用）。
 * progress 是宽口径 dict（OpenAPI 无法表达服务端的自由形状），这里集中
 * 做窄化读取，组件不直接摸 unknown。
 */

/** 水位采样序列（feeder 每次 CAS 推进追加的 {level, ts}，最多 50 个）。 */
export function watermarkSamples(
  campaign: CampaignRecord
): CampaignWatermarkSample[] {
  const raw = campaign.progress?.watermark_samples
  if (!Array.isArray(raw)) return []
  const samples: CampaignWatermarkSample[] = []
  for (const item of raw) {
    if (
      typeof item === 'object' &&
      item !== null &&
      typeof (item as CampaignWatermarkSample).level === 'number' &&
      typeof (item as CampaignWatermarkSample).ts === 'number'
    ) {
      samples.push({ level: item.level, ts: item.ts })
    }
  }
  return samples
}

/**
 * 游标进度的「已处理 / 总量」读数。三形态（设计 §1.4）：
 * - rerun/upgrade filter：processed 计数 + keyset cursor，总量取
 *   创建时 preview 口径不可知，用 processed（单调推进的可见下界）；
 * - rerun/upgrade 显式 ids / submit：offset / item_offset 对
 *   target_spec 的 job_ids / manifest_item_count 求百分比。
 */
export function cursorProgress(campaign: CampaignRecord): {
  processed: number | null
  total: number | null
} {
  const progress = campaign.progress ?? {}
  const spec = campaign.target_spec ?? {}
  if (typeof progress.processed === 'number') {
    return { processed: progress.processed, total: null }
  }
  if (typeof progress.offset === 'number' && Array.isArray(spec.job_ids)) {
    return { processed: progress.offset, total: spec.job_ids.length }
  }
  if (typeof progress.item_offset === 'number') {
    if (Array.isArray(spec.items)) {
      return { processed: progress.item_offset, total: spec.items.length }
    }
    if (typeof spec.manifest_item_count === 'number') {
      return {
        processed: progress.item_offset,
        total: spec.manifest_item_count,
      }
    }
    return { processed: progress.item_offset, total: null }
  }
  return { processed: null, total: null }
}

/** 连续失败计数（瞬态错误的 UI 告警信号，设计 §2.3）。 */
export function consecutiveFailures(campaign: CampaignRecord): number {
  const value = campaign.progress?.consecutive_failures
  return typeof value === 'number' ? value : 0
}

/** submit 模式详情聚合里的关联 run 概览（PR-C 契约；合入前恒为空）。 */
export function campaignRuns(
  campaign: CampaignRecord
): { id: string; status: string; created_count: number; job_count: number }[] {
  const runs = (campaign as { runs?: unknown }).runs
  if (!Array.isArray(runs)) return []
  return runs.filter(
    (
      item
    ): item is {
      id: string
      status: string
      created_count: number
      job_count: number
    } =>
      typeof item === 'object' &&
      item !== null &&
      typeof (item as { id?: unknown }).id === 'string' &&
      typeof (item as { status?: unknown }).status === 'string'
  )
}
