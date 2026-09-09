import { describe, expect, it } from 'vitest'
import {
  campaignRuns,
  consecutiveFailures,
  cursorProgress,
  watermarkSamples,
} from './campaignProgress'
import { makeCampaign } from './testHelpers'

// progress_json 读取辅助的纯函数测试：三形态游标、水位采样窄化、
// 连续失败计数与 submit 关联 runs 的「可能缺席」读取。

describe('cursorProgress', () => {
  it('reads processed for the filter-mode keyset cursor', () => {
    const progress = cursorProgress(
      makeCampaign({ progress: { cursor: 'c|j1', processed: 120 } })
    )
    expect(progress).toEqual({ processed: 120, total: null })
  })

  it('reads offset against the stored job id snapshot', () => {
    const progress = cursorProgress(
      makeCampaign({
        target_spec: { job_ids: ['j1', 'j2', 'j3', 'j4'] },
        progress: { offset: 2 },
      })
    )
    expect(progress).toEqual({ processed: 2, total: 4 })
  })

  it('reads item_offset against inline items', () => {
    const progress = cursorProgress(
      makeCampaign({
        mode: 'submit',
        target_spec: { items: [{}, {}, {}] },
        progress: { item_offset: 1 },
      })
    )
    expect(progress).toEqual({ processed: 1, total: 3 })
  })

  it('reads item_offset against the manifest item count', () => {
    const progress = cursorProgress(
      makeCampaign({
        mode: 'submit',
        target_spec: {
          manifest_storage_key: 'ws1/x',
          manifest_item_count: 100,
        },
        progress: { item_offset: 40 },
      })
    )
    expect(progress).toEqual({ processed: 40, total: 100 })
  })

  it('returns nulls when progress is empty', () => {
    expect(cursorProgress(makeCampaign({ progress: {} }))).toEqual({
      processed: null,
      total: null,
    })
  })
})

describe('watermarkSamples', () => {
  it('keeps valid samples and drops malformed entries', () => {
    const samples = watermarkSamples(
      makeCampaign({
        progress: {
          watermark_samples: [
            { level: 100, ts: 1 },
            { level: 'bad', ts: 2 },
            null,
            { level: 200, ts: 3 },
          ],
        },
      })
    )
    expect(samples).toEqual([
      { level: 100, ts: 1 },
      { level: 200, ts: 3 },
    ])
  })

  it('returns an empty array when absent', () => {
    expect(watermarkSamples(makeCampaign({ progress: {} }))).toEqual([])
  })
})

describe('consecutiveFailures', () => {
  it('reads the alerting counter', () => {
    expect(
      consecutiveFailures(
        makeCampaign({ progress: { consecutive_failures: 2 } })
      )
    ).toBe(2)
    expect(consecutiveFailures(makeCampaign())).toBe(0)
  })
})

describe('campaignRuns', () => {
  it('returns the submit-mode run overview when present', () => {
    const runs = [
      { id: 'r1', status: 'created', created_count: 5, job_count: 5 },
    ]
    expect(
      campaignRuns(makeCampaign({ mode: 'submit', ...{ runs } } as never))
    ).toEqual(runs)
  })

  it('returns an empty array when the field is absent (PR-A baseline)', () => {
    expect(campaignRuns(makeCampaign({ mode: 'submit' }))).toEqual([])
    expect(campaignRuns(makeCampaign({ mode: 'rerun' }))).toEqual([])
  })
})
