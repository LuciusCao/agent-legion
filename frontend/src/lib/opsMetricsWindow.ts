import type { MetricBucket, OpsGranularity } from '../api/metrics'
import type { WindowBucket } from './opsMetricsBuckets'
import { nullBucket } from './opsMetricsBuckets'

const STEP_MS: Record<OpsGranularity, number> = {
  '6h': 60_000, // 1 分钟桶
  '24h': 300_000, // 5 分钟桶
  '30d': 14_400_000, // 4 小时桶
}

const BUCKET_COUNT: Record<OpsGranularity, number> = {
  '6h': 360,
  '24h': 288,
  '30d': 180,
}

/**
 * 把后端返回的稀疏 bucket 补齐成固定长度的时间网格，缺失的桶指标字段填 null。
 *
 * 网格对齐到 UTC 桶边界（与后端 epoch-floor 汇总一致）：6h（1 分钟桶）结束于
 * 上一个已完成分钟（采样器只写已完成的分钟）；24h（5 分钟桶）/ 30d（4 小时桶）
 * 结束于当前进行中的桶（聚合自已有分钟行，进行中的桶显示为部分数据）。
 * 这样切换 Worker 或 Worker 数据稀疏时，图表 X 轴窗口保持不变。
 */
export function fillWindowBuckets(
  buckets: MetricBucket[],
  granularity: OpsGranularity,
  now: number = Date.now()
): WindowBucket[] {
  const step = STEP_MS[granularity]
  const aligned = Math.floor(now / step) * step
  const end = granularity === '6h' ? aligned - step : aligned
  const start = end - (BUCKET_COUNT[granularity] - 1) * step
  const byStart = new Map(
    buckets.map((bucket) => [new Date(bucket.bucket_start).getTime(), bucket])
  )
  const filled: WindowBucket[] = []
  for (let t = start; t <= end; t += step) {
    filled.push(byStart.get(t) ?? nullBucket(t))
  }
  return filled
}
