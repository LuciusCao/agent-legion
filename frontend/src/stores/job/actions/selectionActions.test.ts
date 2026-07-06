import { describe, it, expect, beforeEach } from 'vitest'
import { useJobStore } from '../index'
import { createJobSummary } from './testHelpers'

describe('selectionActions', () => {
  beforeEach(() => {
    useJobStore.setState({
      jobs: [],
      selectedIds: new Set(),
      filterConfig: {
        status: null,
        search: '',
        workflowVersion: null,
        activeNodeKey: null,
      },
    })
  })

  it('selectUnpacked selects only completed jobs that are not packed', () => {
    useJobStore.setState({
      jobs: [
        createJobSummary({ id: 'j1', status: 'completed', packed: 0 }),
        createJobSummary({ id: 'j2', status: 'completed', packed: 1 }),
        createJobSummary({ id: 'j3', status: 'failed', packed: 0 }),
        createJobSummary({ id: 'j4', status: 'completed', packed: 0 }),
      ],
    })

    useJobStore.getState().selectUnpacked()

    expect(useJobStore.getState().selectedIds).toEqual(new Set(['j1', 'j4']))
  })

  it('selectUnpacked respects current visible filters', () => {
    useJobStore.setState({
      jobs: [
        createJobSummary({ id: 'j1', status: 'completed', packed: 0 }),
        createJobSummary({ id: 'j2', status: 'completed', packed: 0 }),
      ],
      filterConfig: {
        status: null,
        search: 'j1',
        workflowVersion: null,
        activeNodeKey: null,
      },
    })

    useJobStore.getState().selectUnpacked()

    expect(useJobStore.getState().selectedIds).toEqual(new Set(['j1']))
  })
})
