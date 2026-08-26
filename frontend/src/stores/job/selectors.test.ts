import { describe, it, expect } from 'vitest'
import { getVisibleJobs } from './filterLogic/getVisibleJobs'
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
