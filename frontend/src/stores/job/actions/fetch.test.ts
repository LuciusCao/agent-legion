import { describe, it, expect } from 'vitest'
import { resetForWorkspace, failJobFetch } from './fetch'
import { createJobSummary, createJobState } from './testHelpers'

describe('resetForWorkspace', () => {
  it('clears jobs, sets loading, and clears selection for the new workspace', () => {
    const state = createJobState({
      jobs: [createJobSummary({ id: 'j1', workspace_id: 'ws1' })],
      jobsWorkspaceId: 'ws1',
      isLoading: false,
      error: 'boom',
      selectedIds: new Set(['j1']),
      filterConfig: {
        status: 'failed',
        search: 'algebra',
        workflowVersion: 4,
        activeNodeKey: 'review',
      },
    })

    const next = resetForWorkspace('ws2')(state)

    expect(next.jobs).toEqual([])
    expect(next.isLoading).toBe(true)
    expect(next.jobsWorkspaceId).toBe('ws2')
    expect(next.error).toBeNull()
    expect(next.selectedIds).toEqual(new Set())
    expect(next.filterConfig).toEqual({
      status: null,
      search: '',
      workflowVersion: null,
      activeNodeKey: null,
    })
  })

  it('preserves selection and filters when jobsWorkspaceId matches target workspace', () => {
    const state = createJobState({
      jobsWorkspaceId: 'ws1',
      jobs: [createJobSummary({ id: 'j1', workspace_id: 'ws1' })],
      selectedIds: new Set(['j1']),
      filterConfig: {
        status: 'completed',
        search: 'geometry',
        workflowVersion: 3,
        activeNodeKey: 'generate',
      },
    })

    const next = resetForWorkspace('ws1')(state)

    expect(next.jobs).toEqual([])
    expect(next.selectedIds).toEqual(new Set(['j1']))
    expect(next.filterConfig).toEqual(state.filterConfig)
  })

  it('preserves selectedIds when all jobs belong to target workspace and clears jobs', () => {
    const state = createJobState({
      jobsWorkspaceId: null,
      jobs: [createJobSummary({ id: 'j1', workspace_id: 'ws1' })],
      selectedIds: new Set(['j1']),
    })

    const next = resetForWorkspace('ws1')(state)

    expect(next.jobs).toEqual([])
    expect(next.selectedIds).toEqual(new Set(['j1']))
  })
})

describe('failJobFetch', () => {
  it('sets error and clears loading/jobs when jobsWorkspaceId matches', () => {
    const state = createJobState({
      jobsWorkspaceId: 'ws1',
      isLoading: true,
      jobs: [createJobSummary({ id: 'j1', workspace_id: 'ws1' })],
    })

    const next = failJobFetch('ws1', 'boom')(state)

    expect(next.error).toBe('boom')
    expect(next.isLoading).toBe(false)
    expect(next.jobs).toEqual([])
  })

  it('returns empty update when jobsWorkspaceId does not match', () => {
    const state = createJobState({
      jobsWorkspaceId: 'ws2',
      isLoading: true,
    })

    const next = failJobFetch('ws1', 'boom')(state)

    expect(next).toEqual({})
  })
})
