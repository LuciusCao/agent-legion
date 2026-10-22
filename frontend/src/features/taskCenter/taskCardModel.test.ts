import { describe, expect, it } from 'vitest'
import {
  estimateRemaining,
  formatDayTime,
  formatDuration,
  jobBatchOpToCard,
  runBatchToCard,
  shortOpRef,
  shortRunRef,
  topReasons,
} from './taskCardModel'
import type { RunBatchListItem } from '../../api/runBatchesApi'
import type { JobBatchOperation } from '../../api/jobBatchOperationsApi'

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

describe('jobBatchOpToCard（#946 / M52）', () => {
  function op(overrides: Partial<JobBatchOperation>): JobBatchOperation {
    return {
      id: 'a1b2c3d4e5f64788',
      workspace_id: 'ws1',
      kind: 'rerun',
      status: 'running',
      items_total: 20000,
      processed_count: 8400,
      succeeded_count: 8000,
      skipped_count: 300,
      failed_count: 100,
      reason_counts: { busy: 250, not_found: 100, internal_error: 50 },
      output: {},
      chunk_errors: [],
      error_message: '',
      created_by: 'tester',
      created_at: '2026-10-22T10:00:00+00:00',
      updated_at: '2026-10-22T10:03:00+00:00',
      finished_at: null,
      ...overrides,
    } as JobBatchOperation
  }

  it('maps states onto card statuses; completed+失败 = partial', () => {
    expect(
      jobBatchOpToCard(op({ status: 'pending', processed_count: 0 })).status
    ).toBe('queued')
    // pending 且有处理量 = tick 间隙，实为进行中
    expect(
      jobBatchOpToCard(op({ status: 'pending', processed_count: 400 })).status
    ).toBe('running')
    expect(jobBatchOpToCard(op({ status: 'running' })).status).toBe('running')
    expect(jobBatchOpToCard(op({ status: 'paused' })).status).toBe('paused')
    expect(jobBatchOpToCard(op({ status: 'cancelled' })).status).toBe(
      'cancelled'
    )
    expect(jobBatchOpToCard(op({ status: 'failed' })).status).toBe('failed')
    expect(
      jobBatchOpToCard(op({ status: 'completed', failed_count: 0 })).status
    ).toBe('done')
    expect(
      jobBatchOpToCard(op({ status: 'completed', failed_count: 5 })).status
    ).toBe('partial')
  })

  it('uses the per-kind meta（图标/标题/成功计数标签）', () => {
    expect(jobBatchOpToCard(op({})).icon).toBe('restart_alt')
    expect(jobBatchOpToCard(op({})).title).toBe('批量重跑')
    expect(jobBatchOpToCard(op({})).okLabel).toBe('重跑')
    expect(jobBatchOpToCard(op({ kind: 'delete' })).icon).toBe('delete')
    expect(jobBatchOpToCard(op({ kind: 'package' })).icon).toBe('archive')
    expect(jobBatchOpToCard(op({ kind: 'upgrade_workflow' })).icon).toBe(
      'arrow_circle_up'
    )
    expect(jobBatchOpToCard(op({ kind: 'clear_packed' })).icon).toBe(
      'unarchive'
    )
  })

  it('summarizes reason_counts into translated Top 3 lines', () => {
    const card = jobBatchOpToCard(op({}))
    expect(card.reasons).toEqual([
      { label: '正在执行中', count: 250 },
      { label: '任务不存在', count: 100 },
      { label: '内部错误', count: 50 },
    ])
    // 未收录的码原样展示
    expect(topReasons({ brand_new_code: 2 })).toEqual([
      { label: 'brand_new_code', count: 2 },
    ])
    expect(topReasons({ a: 1, b: 2, c: 3, d: 4 })).toHaveLength(3)
  })

  it('carries the package download url only from terminal output', () => {
    expect(
      jobBatchOpToCard(
        op({ kind: 'package', output: { download_url: '/api/x/pkg.zip' } })
      ).downloadUrl
    ).toBe('/api/x/pkg.zip')
    expect(
      jobBatchOpToCard(op({ kind: 'package', output: {} })).downloadUrl
    ).toBeNull()
    expect(jobBatchOpToCard(op({})).downloadUrl).toBeNull()
  })

  it('never exposes pause/cancel（控制面本期不外露）nor archive provenance', () => {
    const card = jobBatchOpToCard(op({}))
    expect(card.controllable).toBe(false)
    expect(card.archivedAt).toBeNull()
    expect(card.archiveSource).toBeNull()
    expect(card.refId).toBe('op-a1b2c3d4')
    expect(shortOpRef('op-already')).toBe('op-already')
  })

  it('run-batch cards stay controllable with the add_task icon', () => {
    const card = runBatchToCard(batch({ status: 'running' }))
    expect(card.controllable).toBe(true)
    expect(card.icon).toBe('add_task')
  })
})

describe('card formatting helpers', () => {
  // formatDayTime 按浏览器本地时区渲染「今天/昨天」；样本一律用本地时区
  // 原语 new Date(y, m, d, …) 构造，断言在任何进程 TZ 下成立。不能用
  // vi.stubEnv('TZ') 钉时区：vitest 以 worker_threads 跑测试
  // （--pool=threads），worker 内 process.env 是快照副本、不触达进程级
  // C environ 的 tzset，Linux 上 Date 时区永远取进程启动时的 TZ
  // （vitest.config test.env 同理无效）——macOS 本地碰巧生效，Linux CI
  // 红（main run 39571319114）。
  const localIso = (y: number, m: number, d: number, hh: number, mm: number) =>
    new Date(y, m - 1, d, hh, mm).toISOString()

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
    const now = new Date(2026, 9, 21, 12, 0)
    expect(formatDayTime(localIso(2026, 10, 21, 10, 21), now)).toBe(
      '今天 10:21'
    )
    expect(formatDayTime(localIso(2026, 10, 20, 17, 40), now)).toBe(
      '昨天 17:40'
    )
    expect(formatDayTime(localIso(2026, 10, 14, 14, 0), now)).toBe('10月14日')
  })
})
