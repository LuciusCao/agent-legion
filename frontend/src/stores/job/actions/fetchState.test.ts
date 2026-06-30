import { describe, it, expect } from 'vitest'
import {
  resetForWorkspace,
  startJobFetch,
  finishJobFetch,
  failJobFetch,
} from './fetchState'
import { createJobSummary, createJobState } from './testHelpers'

describe('resetForWorkspace', () => {
  it('clears jobs, sets loading, and clears selection for the new workspace', () => {
    const state = createJobState({
      jobs: [createJobSummary({ id: 'j1', workspace_id: 'ws1' })],
      jobsWorkspaceId: 'ws1',
      isLoading: false,
      error: 'boom',
      selectedIds: new Set(['j1']),
    })

    const next = resetForWorkspace('ws2')(state)

    expect(next.jobs).toEqual([])
    expect(next.isLoading).toBe(true)
    expect(next.jobsWorkspaceId).toBe('ws2')
    expect(next.error).toBeNull()
    expect(next.selectedIds).toEqual(new Set())
  })
})

describe('startJobFetch', () => {
  it('preserves selectedIds when jobsWorkspaceId matches target workspace', () => {
    const state = createJobState({
      jobsWorkspaceId: 'ws1',
      selectedIds: new Set(['j1']),
    })

    const next = startJobFetch('ws1')(state)

    expect(next.selectedIds).toEqual(new Set(['j1']))
    expect(next.isLoading).toBe(true)
    expect(next.jobsWorkspaceId).toBe('ws1')
  })

  it('preserves selectedIds when all jobs belong to target workspace', () => {
    const state = createJobState({
      jobsWorkspaceId: null,
      jobs: [
        createJobSummary({ id: 'j1', workspace_id: 'ws1' }),
        createJobSummary({ id: 'j2', workspace_id: 'ws1' }),
      ],
      selectedIds: new Set(['j1', 'j2']),
    })

    const next = startJobFetch('ws1')(state)

    expect(next.selectedIds).toEqual(new Set(['j1', 'j2']))
  })

  it('clears selectedIds when switching workspaces', () => {
    const state = createJobState({
      jobsWorkspaceId: 'ws1',
      selectedIds: new Set(['j1']),
    })

    const next = startJobFetch('ws2')(state)

    expect(next.selectedIds).toEqual(new Set())
  })
})

describe('finishJobFetch', () => {
  it('updates state when jobsWorkspaceId matches', () => {
    const jobs = [createJobSummary({ id: 'j1', workspace_id: 'ws1' })]
    const state = createJobState({
      jobsWorkspaceId: 'ws1',
      isLoading: true,
      error: 'boom',
    })

    const next = finishJobFetch('ws1', jobs)(state)

    expect(next.jobs).toEqual(jobs)
    expect(next.isLoading).toBe(false)
    expect(next.error).toBeNull()
  })

  it('returns empty update when jobsWorkspaceId does not match', () => {
    const jobs = [createJobSummary({ id: 'j1', workspace_id: 'ws1' })]
    const state = createJobState({
      jobsWorkspaceId: 'ws2',
      isLoading: true,
    })

    const next = finishJobFetch('ws1', jobs)(state)

    expect(next).toEqual({})
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
