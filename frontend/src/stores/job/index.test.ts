import { describe, it, expect } from 'vitest'
import { useJobStore } from './index'
import { createJobSummary } from './actions/testHelpers'

describe('useJobStore', () => {
  it('exposes resetForWorkspace', () => {
    expect(typeof useJobStore.getState().resetForWorkspace).toBe('function')
  })

  it('resets state for a workspace', () => {
    useJobStore.setState({
      jobs: [createJobSummary({ id: 'j1', workspace_id: 'ws1' })],
      jobsWorkspaceId: 'ws1',
      isLoading: false,
      selectedIds: new Set(['j1']),
    })

    useJobStore.getState().resetForWorkspace('ws2')

    const state = useJobStore.getState()
    expect(state.jobs).toEqual([])
    expect(state.isLoading).toBe(true)
    expect(state.jobsWorkspaceId).toBe('ws2')
    expect(state.selectedIds).toEqual(new Set())
  })
})
