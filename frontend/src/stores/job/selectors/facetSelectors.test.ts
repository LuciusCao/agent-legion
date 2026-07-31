import { describe, it, expect, beforeEach } from 'vitest'
import { useJobStore } from '../index'
import { selectFilterCounts } from './filterSelectors'
import {
  makeSelectNodeOptions,
  selectWorkflowVersionOptions,
} from './optionSelectors'
import type { JobFacetsResponse } from '../../../types/jobTypes'

const facets: JobFacetsResponse = {
  workspace_id: 'ws1',
  total: 4,
  status_counts: { pending: 1, running: 2, failed: 1 },
  version_counts: { '2': 3, none: 1 },
  node_counts: { extract: 3, generate: 1, '': 0 },
}

describe('facet selectors', () => {
  beforeEach(() => {
    useJobStore.setState({
      facets,
      jobsWorkspaceId: 'ws1',
      filterCounts: { status: {}, workflowVersion: {}, activeNodeKey: {} },
    })
  })

  it('maps facet counts to filter counts with per-dimension totals', () => {
    const counts = selectFilterCounts(useJobStore.getState())

    expect(counts.status).toEqual({
      pending: 1,
      running: 2,
      failed: 1,
      all: 4,
    })
    expect(counts.workflowVersion).toEqual({ '2': 3, none: 1, all: 4 })
    expect(counts.activeNodeKey).toEqual({
      extract: 3,
      generate: 1,
      '': 0,
      all: 4,
    })
  })

  it('keeps reference stability for the same facets object', () => {
    const first = selectFilterCounts(useJobStore.getState())
    const second = selectFilterCounts(useJobStore.getState())
    expect(second).toBe(first)
  })

  it('derives workflow version options from facets', () => {
    expect(selectWorkflowVersionOptions(useJobStore.getState())).toEqual({
      versionOptions: [2],
      hasMissingVersion: true,
    })
  })

  it('derives node options from facet node keys, skipping empty keys', () => {
    const selectNodeOptions = makeSelectNodeOptions(null)

    expect(selectNodeOptions(useJobStore.getState())).toEqual([
      { key: 'extract', label: 'extract' },
      { key: 'generate', label: 'generate' },
    ])
  })
})
