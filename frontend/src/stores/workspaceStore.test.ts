import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useWorkspaceStore } from './workspaceStore'

vi.mock('../api', () => ({
  fetchWorkspaces: vi.fn(),
  createWorkspace: vi.fn(),
  deleteWorkspace: vi.fn(),
  fetchWorkspaceStats: vi.fn(),
}))

beforeEach(() => {
  useWorkspaceStore.setState({
    workspaces: [],
    currentWorkspace: null,
    workspaceStats: {},
    loading: false,
    error: null,
  })
})

describe('workspaceStore', () => {
  it('initial state is empty', () => {
    const s = useWorkspaceStore.getState()
    expect(s.workspaces).toEqual([])
    expect(s.currentWorkspace).toBeNull()
    expect(s.workspaceStats).toEqual({})
  })
})
