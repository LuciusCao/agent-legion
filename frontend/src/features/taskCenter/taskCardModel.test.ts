import { describe, expect, it } from 'vitest'
import {
  estimateRemaining,
  formatDayTime,
  formatDuration,
  runBatchToCard,
  shortRunRef,
} from './taskCardModel'
import type { RunBatchListItem } from '../../api/runBatchesApi'

function batch(overrides: Partial<RunBatchListItem>): RunBatchListItem {
  return {
    run_id: 'abcdef1234567890',
    status: 'running',
    items_total: 2400,
    processed_count: 1200,
    jobs_created: 1000,
    chunk_errors: [],
    error_message: '',
    created_at: '2026-10-21T10:21:00+00:00',
    updated_at: '2026-10-21T10:24:00+00:00',
    finished_at: null,
    created_by: 'tester',
    archived_at: null,
    archive_source: null,
    failed_item_count: 0,
    failed_item_count_lower_bound: false,
    ...overrides,
  } as RunBatchListItem
}

describe('runBatchToCard（#1157 phase3）', () => {
  it('maps the feeder states onto card statuses', () => {
    expect(runBatchToCard(batch({ status: 'pending' })).status).toBe('queued')
    expect(runBatchToCard(batch({ status: 'running' })).status).toBe('running')
    expect(runBatchToCard(batch({ status: 'paused' })).status).toBe('paused')
    expect(runBatchToCard(batch({ status: 'cancelled' })).status).toBe(
      'cancelled'
    )
    expect(runBatchToCard(batch({ status: 'failed' })).status).toBe('failed')
    expect(runBatchToCard(batch({ status: 'completed' })).status).toBe('done')
  })

  it('derives skipped = processed − created − chunk-failed（reviewer r1 P2-1）', () => {
    const done = runBatchToCard(batch({ status: 'completed' }))
    expect(done.skippedCount).toBe(200)
    expect(done.okCount).toBe(1000)
    expect(done.failedCount).toBe(0)

    // processed 2400 / created 2000 / chunk 失败 400 → 跳过 0、失败 400；
    // chunk 失败条目不再被贴「已有任务」标签。
    const partial = runBatchToCard(
      batch({
        status: 'completed',
        processed_count: 2400,
        jobs_created: 2000,
        chunk_errors: [{ chunk_start: 2000, error: '连接超时' }],
        failed_item_count: 400,
      })
    )
    expect(partial.status).toBe('partial')
    expect(partial.failedCount).toBe(400)
    expect(partial.failedLowerBound).toBe(false)
    expect(partial.skippedCount).toBe(0)
    expect(partial.errorMessage).toBe('连接超时')

    const lowerBound = runBatchToCard(
      batch({
        status: 'completed',
        chunk_errors: [{ chunk_start: 0, error: 'x' }],
        failed_item_count: 10000,
        failed_item_count_lower_bound: true,
      })
    )
    expect(lowerBound.failedLowerBound).toBe(true)
  })

  it('keeps the archive provenance fields', () => {
    const manual = runBatchToCard(
      batch({
        status: 'completed',
        archived_at: '2026-10-22T01:00:00+00:00',
        archive_source: 'manual',
      })
    )
    expect(manual.archiveSource).toBe('manual')
    expect(manual.archivedAt).not.toBeNull()
  })
})

describe('card formatting helpers', () => {
  it('shortens run ids into the mono ref shape（尾段 digest，reviewer r1 P2-5）', () => {
    // run id 前段是 workspace/workflow 前缀（无区分度），取尾 8 位。
    expect(shortRunRef('abcdef1234567890')).toBe('run-34567890')
    expect(shortRunRef('run-already')).toBe('run-already')
  })

  it('formats durations in the prototype wording', () => {
    expect(formatDuration(48)).toBe('48 秒')
    expect(formatDuration(192)).toBe('3 分 12 秒')
    expect(formatDuration(120)).toBe('2 分钟')
    expect(formatDuration(3720)).toBe('1 小时 02 分')
  })

  it('estimates the remaining time linearly', () => {
    expect(estimateRemaining(500, 1000, 120)).toBe('预计剩余约 2 分钟')
    expect(estimateRemaining(0, 1000, 120)).toBeNull()
    expect(estimateRemaining(1000, 1000, 120)).toBeNull()
  })

  it('renders 今天/昨天/日期 by calendar day', () => {
    const now = new Date('2026-10-21T12:00:00+08:00')
    expect(formatDayTime('2026-10-21T02:21:00+00:00', now)).toBe('今天 10:21')
    expect(formatDayTime('2026-10-20T09:40:00+00:00', now)).toBe('昨天 17:40')
    expect(formatDayTime('2026-10-14T06:00:00+00:00', now)).toBe('10月14日')
  })
})
