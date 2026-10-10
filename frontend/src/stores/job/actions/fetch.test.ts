import { describe, it, expect } from 'vitest'
import { resetForWorkspace, failJobFetch } from './fetch'
import { createJobSummary, createJobState } from './testHelpers'

describe('resetForWorkspace', () => {
  it('clears jobs, sets loading, and clears selection for the new workspace', () => {
    const state = createJobState({
      jobs: [createJobSummary({ id: 'j1', workspace_id: 'ws1' })],
      jobsWorkspaceId: 'ws1',
      isLoading: false,
      listLoadError: 'boom',
      selectedIds: new Set(['j1']),
      filterConfig: {
        status: 'failed',
        search: 'algebra',
        workflowVersion: 4,
        activeNodeKey: 'review',
        paused: null,
      },
    })

    const next = resetForWorkspace('ws2')(state)

    expect(next.jobs).toEqual([])
    expect(next.isLoading).toBe(true)
    expect(next.jobsWorkspaceId).toBe('ws2')
    expect(next.listLoadError).toBeNull()
    expect(next.selectedIds).toEqual(new Set())
    expect(next.filterConfig).toEqual({
      status: null,
      search: '',
      workflowVersion: null,
      activeNodeKey: null,
      paused: null,
    })
  })

  it('resets the revision counter so the new workspace starts comparing from zero (#1183)', () => {
    // 切换 workspace 不重置 revision 时，上一个 workspace 残留的高 revision
    // 会把新 workspace 的合法快照全部丢弃（setJobsSnapshotUpdate 守卫），
    // 页面卡 skeleton。
    const state = createJobState({
      jobsWorkspaceId: 'ws1',
      jobs: [createJobSummary({ id: 'j1', workspace_id: 'ws1' })],
      revision: 11922503,
    })

    const next = resetForWorkspace('ws2')(state)

    expect(next.revision).toBe(0)
  })

  it('resets the revision counter when re-entering the same workspace', () => {
    const state = createJobState({
      jobsWorkspaceId: 'ws1',
      jobs: [createJobSummary({ id: 'j1', workspace_id: 'ws1' })],
      revision: 42,
    })

    const next = resetForWorkspace('ws1')(state)

    expect(next.revision).toBe(0)
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
        paused: null,
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
  it('sets listLoadError and clears loading/jobs when jobsWorkspaceId matches', () => {
    const state = createJobState({
      jobsWorkspaceId: 'ws1',
      isLoading: true,
      jobs: [createJobSummary({ id: 'j1', workspace_id: 'ws1' })],
    })

    const next = failJobFetch('ws1', 'boom')(state)

    expect(next.listLoadError).toBe('boom')
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
