import type { MetricBucket, OpsGranularity } from '../api/metrics'

const STEP_MS: Record<OpsGranularity, number> = {
  minute: 60_000,
  hour: 3_600_000,
  day: 86_400_000,
}

const BUCKET_COUNT: Record<OpsGranularity, number> = {
  minute: 360, // 近 6 小时
  hour: 24, // 近 24 小时
  day: 7, // 近 7 天
}

function zeroBucket(startMs: number): MetricBucket {
  return {
    bucket_start: new Date(startMs).toISOString(),
    online_workers: 0,
    online_workers_max: 0,
    active_executions: 0,
    active_executions_max: 0,
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    total_tokens: 0,
  }
}

/**
 * 把后端返回的稀疏 bucket 补齐成固定长度的时间网格，缺失的桶填零。
 *
 * 网格对齐到 UTC 整点（与后端 date_trunc 汇总一致）：分钟粒度结束于上一个
 * 已完成分钟（采样器只写已完成的分钟），小时/天粒度结束于当前未完成的
 * 时段。这样切换 Worker 或 Worker 数据稀疏时，图表 X 轴窗口保持不变。
 */
export function fillWindowBuckets(
  buckets: MetricBucket[],
  granularity: OpsGranularity,
  now: number = Date.now()
): MetricBucket[] {
  const step = STEP_MS[granularity]
  const aligned = Math.floor(now / step) * step
  const end = granularity === 'minute' ? aligned - step : aligned
  const start = end - (BUCKET_COUNT[granularity] - 1) * step
  const byStart = new Map(
    buckets.map((bucket) => [new Date(bucket.bucket_start).getTime(), bucket])
  )
  const filled: MetricBucket[] = []
  for (let t = start; t <= end; t += step) {
    filled.push(byStart.get(t) ?? zeroBucket(t))
  }
  return filled
}
