import { describe, it, expect } from 'vitest'
import { countJobsByFailureCategory } from './failureCategoryCounts'
import type { FailedNodeRunItem } from '../../types/failureTypes'

function makeRun(overrides: Partial<FailedNodeRunItem>): FailedNodeRunItem {
  return {
    job_id: 'j1',
    node_key: 'extract',
    node_run_id: 1,
    workflow_key: 'wf',
    failure_category: 'technical',
    failure_detail: 'timeout',
    error_message: 'boom',
    finished_at: '2026-07-01T00:00:00Z',
    ...overrides,
  }
}

describe('countJobsByFailureCategory', () => {
  it('counts each selected job by its failure category', () => {
    const runs = [
      makeRun({ job_id: 'j1', failure_category: 'technical' }),
      makeRun({ job_id: 'j2', failure_category: 'business' }),
      makeRun({ job_id: 'j3', failure_category: 'unknown' }),
    ]
    expect(countJobsByFailureCategory(runs, ['j1', 'j2', 'j3'])).toEqual({
      technical: 1,
      business: 1,
      unknown: 1,
    })
  })

  it('ignores runs of jobs outside the selection', () => {
    const runs = [
      makeRun({ job_id: 'j1', failure_category: 'technical' }),
      makeRun({ job_id: 'j2', failure_category: 'business' }),
    ]
    expect(countJobsByFailureCategory(runs, ['j1'])).toEqual({
      technical: 1,
      business: 0,
      unknown: 0,
    })
  })

  it('dedupes multiple failed nodes of one job by the latest finished_at', () => {
    const runs = [
      makeRun({
        job_id: 'j1',
        node_key: 'extract',
        node_run_id: 1,
        failure_category: 'technical',
        finished_at: '2026-07-01T00:00:00Z',
      }),
      makeRun({
        job_id: 'j1',
        node_key: 'review',
        node_run_id: 2,
        failure_category: 'business',
        finished_at: '2026-07-02T00:00:00Z',
      }),
    ]
    expect(countJobsByFailureCategory(runs, ['j1'])).toEqual({
      technical: 0,
      business: 1,
      unknown: 0,
    })
  })

  it('falls back to node_run_id when finished_at is missing', () => {
    const runs = [
      makeRun({
        job_id: 'j1',
        node_key: 'extract',
        node_run_id: 1,
        failure_category: 'technical',
        finished_at: null,
      }),
      makeRun({
        job_id: 'j1',
        node_key: 'review',
        node_run_id: 2,
        failure_category: 'business',
        finished_at: null,
      }),
    ]
    expect(countJobsByFailureCategory(runs, ['j1'])).toEqual({
      technical: 0,
      business: 1,
      unknown: 0,
    })
  })

  it('prefers the run with finished_at over one without', () => {
    const runs = [
      makeRun({
        job_id: 'j1',
        node_run_id: 5,
        failure_category: 'technical',
        finished_at: '2026-07-01T00:00:00Z',
      }),
      makeRun({
        job_id: 'j1',
        node_run_id: 9,
        failure_category: 'business',
        finished_at: null,
      }),
    ]
    expect(countJobsByFailureCategory(runs, ['j1'])).toEqual({
      technical: 1,
      business: 0,
      unknown: 0,
    })
  })

  it('groups unrecognized categories into unknown', () => {
    const runs = [makeRun({ job_id: 'j1', failure_category: 'weird' })]
    expect(countJobsByFailureCategory(runs, ['j1'])).toEqual({
      technical: 0,
      business: 0,
      unknown: 1,
    })
  })

  it('returns zeros for empty input', () => {
    expect(countJobsByFailureCategory([], [])).toEqual({
      technical: 0,
      business: 0,
      unknown: 0,
    })
  })
})
