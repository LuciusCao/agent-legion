import { describe, it, expect } from 'vitest'
import { getVisibleJobs } from './filterLogic/getVisibleJobs'
import { getFilterCounts } from './filterLogic/getFilterCounts'
import { createJobState, createJobSummary } from './actions/testHelpers'

describe('getVisibleJobs', () => {
  it('filters by status', () => {
    const state = createJobState({
      jobs: [
        createJobSummary({ id: 'j1', status: 'running' }),
        createJobSummary({ id: 'j2', status: 'failed' }),
      ],
      filterConfig: {
        status: 'failed',
        search: '',
        workflowVersion: null,
        activeNodeKey: null,
        paused: null,
      },
    })
    expect(getVisibleJobs(state).map((j) => j.id)).toEqual(['j2'])
  })

  it('filters paused jobs separately from pending jobs', () => {
    const state = createJobState({
      jobs: [
        createJobSummary({ id: 'j1', status: 'paused' }),
        createJobSummary({ id: 'j2', status: 'pending' }),
      ],
      filterConfig: {
        status: 'paused',
        search: '',
        workflowVersion: null,
        activeNodeKey: null,
        paused: null,
      },
    })
    expect(getVisibleJobs(state).map((j) => j.id)).toEqual(['j1'])
  })

  it('filters by workflow version', () => {
    const state = createJobState({
      jobs: [
        createJobSummary({ id: 'j1', workflow_version: 3 }),
        createJobSummary({ id: 'j2', workflow_version: 2 }),
      ],
      filterConfig: {
        status: null,
        search: '',
        workflowVersion: 2,
        activeNodeKey: null,
        paused: null,
      },
    })
    expect(getVisibleJobs(state).map((j) => j.id)).toEqual(['j2'])
  })

  it('filters by missing workflow version', () => {
    const state = createJobState({
      jobs: [
        createJobSummary({ id: 'j1', workflow_version: 3 }),
        createJobSummary({ id: 'j2' }),
      ],
      filterConfig: {
        status: null,
        search: '',
        workflowVersion: 'none',
        activeNodeKey: null,
        paused: null,
      },
    })
    expect(getVisibleJobs(state).map((j) => j.id)).toEqual(['j2'])
  })

  it('filters by active node key', () => {
    const state = createJobState({
      jobs: [
        createJobSummary({ id: 'j1', active_node_key: 'extract' }),
        createJobSummary({ id: 'j2', active_node_key: 'review' }),
      ],
      filterConfig: {
        status: null,
        search: '',
        workflowVersion: null,
        activeNodeKey: 'review',
        paused: null,
      },
    })
    expect(getVisibleJobs(state).map((j) => j.id)).toEqual(['j2'])
  })

  it('searches across id, source_id, batch_id and title', () => {
    const state = createJobState({
      jobs: [
        createJobSummary({
          id: 'job-101',
          source_id: 'Q100',
          batch_id: 'B1',
          title: 'Algebra',
        }),
        createJobSummary({
          id: 'job-102',
          source_id: 'Q200',
          batch_id: 'B2',
          title: 'Geometry',
        }),
      ],
      filterConfig: {
        status: null,
        search: 'B2',
        workflowVersion: null,
        activeNodeKey: null,
        paused: null,
      },
    })
    expect(getVisibleJobs(state).map((j) => j.id)).toEqual(['job-102'])
  })

  it('combines multiple filters', () => {
    const state = createJobState({
      jobs: [
        createJobSummary({ id: 'j1', status: 'failed', workflow_version: 3 }),
        createJobSummary({ id: 'j2', status: 'failed', workflow_version: 2 }),
        createJobSummary({ id: 'j3', status: 'running', workflow_version: 3 }),
      ],
      filterConfig: {
        status: 'failed',
        search: '',
        workflowVersion: 3,
        activeNodeKey: null,
        paused: null,
      },
    })
    expect(getVisibleJobs(state).map((j) => j.id)).toEqual(['j1'])
  })
})

describe('getFilterCounts', () => {
  it('counts status excluding the status filter itself', () => {
    const state = createJobState({
      jobs: [
        createJobSummary({ id: 'j1', status: 'running' }),
        createJobSummary({ id: 'j2', status: 'failed' }),
        createJobSummary({ id: 'j3', status: 'failed' }),
      ],
      filterConfig: {
        status: 'failed',
        search: '',
        workflowVersion: null,
        activeNodeKey: null,
        paused: null,
      },
    })
    const counts = getFilterCounts(state)
    expect(counts.status.running).toBe(1)
    expect(counts.status.failed).toBe(2)
  })

  it('counts paused jobs separately from pending jobs', () => {
    const state = createJobState({
      jobs: [
        createJobSummary({ id: 'j1', status: 'paused' }),
        createJobSummary({ id: 'j2', status: 'pending' }),
      ],
      filterConfig: {
        status: null,
        search: '',
        workflowVersion: null,
        activeNodeKey: null,
        paused: null,
      },
    })
    const counts = getFilterCounts(state)
    expect(counts.status.paused).toBe(1)
    expect(counts.status.pending).toBe(1)
  })

  it('counts all statuses when status filter is all', () => {
    const state = createJobState({
      jobs: [
        createJobSummary({ id: 'j1', status: 'running' }),
        createJobSummary({ id: 'j2', status: 'failed' }),
        createJobSummary({ id: 'j3', status: 'failed' }),
      ],
      filterConfig: {
        status: null,
        search: '',
        workflowVersion: null,
        activeNodeKey: null,
        paused: null,
      },
    })
    const counts = getFilterCounts(state)
    expect(counts.status.all).toBe(3)
  })

  it('counts versions excluding the version filter', () => {
    const state = createJobState({
      jobs: [
        createJobSummary({ id: 'j1', workflow_version: 3 }),
        createJobSummary({ id: 'j2', workflow_version: 2 }),
        createJobSummary({ id: 'j3', workflow_version: 2 }),
      ],
      filterConfig: {
        status: null,
        search: '',
        workflowVersion: 2,
        activeNodeKey: null,
        paused: null,
      },
    })
    const counts = getFilterCounts(state)
    expect(counts.workflowVersion['3']).toBe(1)
    expect(counts.workflowVersion['2']).toBe(2)
  })

  it('counts active node keys excluding the node filter', () => {
    const state = createJobState({
      jobs: [
        createJobSummary({ id: 'j1', active_node_key: 'extract' }),
        createJobSummary({ id: 'j2', active_node_key: 'review' }),
      ],
      filterConfig: {
        status: null,
        search: '',
        workflowVersion: null,
        activeNodeKey: 'extract',
        paused: null,
      },
    })
    const counts = getFilterCounts(state)
    expect(counts.activeNodeKey.extract).toBe(1)
    expect(counts.activeNodeKey.review).toBe(1)
  })

  it('counts all options using total jobs matching other filters', () => {
    const state = createJobState({
      jobs: [
        createJobSummary({ id: 'j1', status: 'running', workflow_version: 3 }),
        createJobSummary({
          id: 'j2',
          status: 'completed',
          workflow_version: 2,
        }),
        createJobSummary({ id: 'j3', status: 'failed' }),
        createJobSummary({
          id: 'j4',
          status: 'pending',
          active_node_key: 'extract',
        }),
      ],
      filterConfig: {
        status: null,
        search: '',
        workflowVersion: null,
        activeNodeKey: null,
        paused: null,
      },
    })
    const counts = getFilterCounts(state)
    expect(counts.status.all).toBe(4)
    expect(counts.workflowVersion.all).toBe(4)
    expect(counts.workflowVersion['3']).toBe(1)
    expect(counts.workflowVersion['2']).toBe(1)
    expect(counts.workflowVersion.none).toBe(2)
    expect(counts.activeNodeKey.all).toBe(4)
    expect(counts.activeNodeKey.extract).toBe(1)
  })
})
