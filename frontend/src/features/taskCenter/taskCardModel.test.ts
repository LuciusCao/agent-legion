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

  it('derives skipped = processed − created and marks chunk-error completions partial', () => {
    const done = runBatchToCard(batch({ status: 'completed' }))
    expect(done.skippedCount).toBe(200)
    expect(done.okCount).toBe(1000)

    const partial = runBatchToCard(
      batch({
        status: 'completed',
        chunk_errors: [{ index: 3, error: '连接超时' }],
      })
    )
    expect(partial.status).toBe('partial')
    expect(partial.errorMessage).toBe('连接超时')
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
  it('shortens run ids into the mono ref shape', () => {
    expect(shortRunRef('abcdef1234567890')).toBe('run-abcdef12')
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
