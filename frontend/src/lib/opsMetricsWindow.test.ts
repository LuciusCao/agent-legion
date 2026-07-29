import { describe, expect, it } from 'vitest'
import { fillWindowBuckets, lastNonNullBucket } from './opsMetricsWindow'
import type { MetricBucket } from '../api/metrics'

const NOW = Date.parse('2026-07-27T10:23:45Z')

function bucket(startIso: string, totalTokens: number): MetricBucket {
  return {
    bucket_start: startIso,
    online_workers: 1,
    online_workers_max: 1,
    active_executions: 1,
    active_executions_max: 1,
    input_tokens: totalTokens,
    output_tokens: 0,
    cache_read_tokens: 0,
    total_tokens: totalTokens,
  }
}

describe('fillWindowBuckets', () => {
  it('6h 生成 360 个 1 分钟桶，结束于上一个已完成分钟', () => {
    const filled = fillWindowBuckets([], '6h', NOW)
    expect(filled).toHaveLength(360)
    expect(filled[359].bucket_start).toBe('2026-07-27T10:22:00.000Z')
    expect(filled[0].bucket_start).toBe('2026-07-27T04:23:00.000Z')
    expect(filled[0].total_tokens).toBeNull()
  })

  it('24h 生成 288 个 5 分钟桶，结束于当前进行中的桶', () => {
    const filled = fillWindowBuckets([], '24h', NOW)
    expect(filled).toHaveLength(288)
    expect(filled[287].bucket_start).toBe('2026-07-27T10:20:00.000Z')
    expect(filled[0].bucket_start).toBe('2026-07-26T10:25:00.000Z')
  })

  it('30d 生成 180 个 4 小时桶，对齐 00/04/08… UTC，结束于当前进行中的桶', () => {
    const filled = fillWindowBuckets([], '30d', NOW)
    expect(filled).toHaveLength(180)
    expect(filled[179].bucket_start).toBe('2026-07-27T08:00:00.000Z')
    expect(filled[0].bucket_start).toBe('2026-06-27T12:00:00.000Z')
  })

  it('已有数据的桶保留原值，缺失的桶填 null', () => {
    const existing = bucket('2026-07-27T10:22:00+00:00', 160)
    const filled = fillWindowBuckets([existing], '6h', NOW)
    expect(filled[359].total_tokens).toBe(160)
    expect(filled[358].total_tokens).toBeNull()
    expect(filled[359].online_workers).toBe(1)
    expect(filled[358].online_workers).toBeNull()
  })

  it('稀疏的 Worker 数据不会改变窗口范围', () => {
    const sparse = [bucket('2026-07-27T08:00:00+00:00', 50)]
    const filled = fillWindowBuckets(sparse, '6h', NOW)
    expect(filled).toHaveLength(360)
    const hit = filled.filter((b) => b.total_tokens === 50)
    expect(hit).toHaveLength(1)
    expect(hit[0].bucket_start).toBe('2026-07-27T08:00:00+00:00')
  })

  it('网格之外的旧数据被丢弃', () => {
    const stale = bucket('2026-07-20T00:00:00+00:00', 999)
    const filled = fillWindowBuckets([stale], '6h', NOW)
    expect(filled.every((b) => b.total_tokens !== 999)).toBe(true)
  })
})

describe('lastNonNullBucket', () => {
  it('尾部为 null 填充桶时返回最后一个有真实数据的桶', () => {
    // 尾部桶（上一已完成分钟）采样器尚未写入，被填成 null
    const existing = bucket('2026-07-27T10:21:00+00:00', 160)
    const filled = fillWindowBuckets([existing], '6h', NOW)
    expect(filled[359].total_tokens).toBeNull()
    const latest = lastNonNullBucket(filled)
    expect(latest?.bucket_start).toBe('2026-07-27T10:21:00+00:00')
    expect(latest?.total_tokens).toBe(160)
  })

  it('全部桶都缺数据时返回 null', () => {
    const filled = fillWindowBuckets([], '6h', NOW)
    expect(lastNonNullBucket(filled)).toBeNull()
  })
})
