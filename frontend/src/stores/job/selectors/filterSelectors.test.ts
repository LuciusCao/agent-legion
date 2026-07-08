import { describe, it, expect, beforeEach } from 'vitest'
import { useJobStore } from '../index'
import { selectFilteredJobIds, selectFilterCounts } from './filterSelectors'
import { createJobSummary } from '../actions/testHelpers'

describe('filterSelectors', () => {
  beforeEach(() => {
    useJobStore.setState({
      jobs: [],
      jobsById: {},
      jobIds: [],
      jobIndexById: {},
      revision: 0,
      filteredJobIds: [],
      filterCounts: {
        status: {},
        workflowVersion: {},
        activeNodeKey: {},
      },
      jobsWorkspaceId: null,
    })
  })

  it('recomputes filtered ids when an updated job no longer matches', () => {
    const store = useJobStore.getState()
    store.setJobsSnapshot('ws1', 1, [
      createJobSummary({ id: 'j1', status: 'pending' }),
      createJobSummary({ id: 'j2', status: 'pending' }),
    ])
    store.setFilterConfig({ status: 'pending' })

    const first = selectFilteredJobIds(useJobStore.getState())
    expect(first).toEqual(['j1', 'j2'])

    store.applyJobPatchBatch(
      'ws1',
      2,
      [createJobSummary({ id: 'j1', status: 'running' })],
      []
    )

    const second = selectFilteredJobIds(useJobStore.getState())
    expect(second).not.toBe(first)
    expect(second).toEqual(['j2'])
  })

  it('recomputes filtered ids when jobs are added', () => {
    const store = useJobStore.getState()
    store.setJobsSnapshot('ws1', 1, [
      createJobSummary({ id: 'j1', status: 'pending' }),
    ])

    const first = selectFilteredJobIds(useJobStore.getState())

    store.applyJobPatchBatch(
      'ws1',
      2,
      [createJobSummary({ id: 'j2', status: 'pending' })],
      []
    )

    const second = selectFilteredJobIds(useJobStore.getState())
    expect(second).not.toBe(first)
    expect(second).toContain('j2')
  })

  it('recomputes filter counts when job content changes', () => {
    const store = useJobStore.getState()
    store.setJobsSnapshot('ws1', 1, [
      createJobSummary({ id: 'j1', status: 'pending' }),
      createJobSummary({ id: 'j2', status: 'pending' }),
    ])

    const first = selectFilterCounts(useJobStore.getState())
    expect(first.status.pending).toBe(2)

    store.applyJobPatchBatch(
      'ws1',
      2,
      [createJobSummary({ id: 'j1', status: 'running' })],
      []
    )

    const second = selectFilterCounts(useJobStore.getState())
    expect(second).not.toBe(first)
    expect(second.status.pending).toBe(1)
  })

  it('caches filtered ids for identical content and filter', () => {
    const store = useJobStore.getState()
    store.setJobsSnapshot('ws1', 1, [
      createJobSummary({ id: 'j1', status: 'pending' }),
    ])

    const first = selectFilteredJobIds(useJobStore.getState())
    const second = selectFilteredJobIds(useJobStore.getState())
    expect(second).toBe(first)
  })
})
