import { describe, expect, it } from 'vitest'
import { fillWindowBuckets } from './opsMetricsWindow'
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
  it('分钟粒度生成 360 桶，结束于上一个已完成分钟', () => {
    const filled = fillWindowBuckets([], 'minute', NOW)
    expect(filled).toHaveLength(360)
    expect(filled[359].bucket_start).toBe('2026-07-27T10:22:00.000Z')
    expect(filled[0].bucket_start).toBe('2026-07-27T04:23:00.000Z')
    expect(filled[0].total_tokens).toBe(0)
  })

  it('小时粒度生成 24 桶，结束于当前小时', () => {
    const filled = fillWindowBuckets([], 'hour', NOW)
    expect(filled).toHaveLength(24)
    expect(filled[23].bucket_start).toBe('2026-07-27T10:00:00.000Z')
    expect(filled[0].bucket_start).toBe('2026-07-26T11:00:00.000Z')
  })

  it('天粒度生成 7 桶，结束于今天', () => {
    const filled = fillWindowBuckets([], 'day', NOW)
    expect(filled).toHaveLength(7)
    expect(filled[6].bucket_start).toBe('2026-07-27T00:00:00.000Z')
    expect(filled[0].bucket_start).toBe('2026-07-21T00:00:00.000Z')
  })

  it('已有数据的桶保留原值，缺失的桶填零', () => {
    const existing = bucket('2026-07-27T10:22:00+00:00', 160)
    const filled = fillWindowBuckets([existing], 'minute', NOW)
    expect(filled[359].total_tokens).toBe(160)
    expect(filled[358].total_tokens).toBe(0)
    expect(filled[359].online_workers).toBe(1)
    expect(filled[358].online_workers).toBe(0)
  })

  it('稀疏的 Worker 数据不会改变窗口范围', () => {
    const sparse = [bucket('2026-07-27T08:00:00+00:00', 50)]
    const filled = fillWindowBuckets(sparse, 'minute', NOW)
    expect(filled).toHaveLength(360)
    const hit = filled.filter((b) => b.total_tokens === 50)
    expect(hit).toHaveLength(1)
    expect(hit[0].bucket_start).toBe('2026-07-27T08:00:00+00:00')
  })

  it('网格之外的旧数据被丢弃', () => {
    const stale = bucket('2026-07-20T00:00:00+00:00', 999)
    const filled = fillWindowBuckets([stale], 'minute', NOW)
    expect(filled.every((b) => b.total_tokens !== 999)).toBe(true)
  })
})
