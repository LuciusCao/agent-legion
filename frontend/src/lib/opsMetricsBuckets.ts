import type { MetricBucket } from '../api/metrics'

type MetricField = keyof Omit<MetricBucket, 'bucket_start'>

/**
 * 固定时间窗网格里的 bucket：指标字段为 null 表示该时段无采样数据
 * （区别于真实的 0），图表据此断线而不是跌零。
 */
export type WindowBucket = Pick<MetricBucket, 'bucket_start'> & {
  [K in MetricField]: MetricBucket[K] | null
}

export function nullBucket(startMs: number): WindowBucket {
  return {
    bucket_start: new Date(startMs).toISOString(),
    online_workers: null,
    online_workers_max: null,
    active_executions: null,
    active_executions_max: null,
    queued: null,
    queued_max: null,
    input_tokens: null,
    output_tokens: null,
    cache_read_tokens: null,
    total_tokens: null,
  }
}

/**
 * 窗口内最后一个有真实数据的 bucket（倒序扫描）。
 *
 * 尾部桶常常是尚未写入的当前时段（null 填充），摘要卡读它会闪零；
 * 读取最后一个非 null 桶可保持稳定。
 */
export function lastNonNullBucket(
  buckets: WindowBucket[]
): WindowBucket | null {
  for (let i = buckets.length - 1; i >= 0; i--) {
    if (buckets[i].total_tokens !== null) return buckets[i]
  }
  return null
}
