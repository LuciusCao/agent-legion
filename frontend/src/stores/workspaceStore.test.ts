import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useWorkspaceStore } from './workspaceStore'
import type { WorkspaceRecord } from '../types'
import type { WorkspaceStats } from '../workspaceTypes'

vi.mock('../api', () => ({
  fetchWorkspaces: vi.fn(),
  createWorkspace: vi.fn(),
  updateWorkspace: vi.fn(),
  deleteWorkspace: vi.fn(),
  fetchWorkspaceStats: vi.fn(),
}))

import {
  fetchWorkspaces,
  createWorkspace,
  updateWorkspace,
  deleteWorkspace,
  fetchWorkspaceStats,
} from '../api'

const mockFetchWorkspaces = vi.mocked(fetchWorkspaces)
const mockCreateWorkspace = vi.mocked(createWorkspace)
const mockUpdateWorkspace = vi.mocked(updateWorkspace)
const mockDeleteWorkspace = vi.mocked(deleteWorkspace)
const mockFetchWorkspaceStats = vi.mocked(fetchWorkspaceStats)

describe('workspaceStore', () => {
  beforeEach(() => {
    useWorkspaceStore.setState({
      workspaces: [],
      currentWorkspace: null,
      workspaceStats: {},
      loading: false,
      error: null,
    })
    mockFetchWorkspaces.mockClear()
    mockCreateWorkspace.mockClear()
    mockUpdateWorkspace.mockClear()
    mockDeleteWorkspace.mockClear()
    mockFetchWorkspaceStats.mockClear()
  })

  it('initial state is empty', () => {
    const s = useWorkspaceStore.getState()
    expect(s.workspaces).toEqual([])
    expect(s.currentWorkspace).toBeNull()
    expect(s.workspaceStats).toEqual({})
  })

  it('fetchWorkspaces sets workspaces on success', async () => {
    const workspaces: WorkspaceRecord[] = [
      {
        id: 'ws1',
        name: 'Test Workspace',
        default_pipeline_key: 'question_content',
        default_entity: 'question',
      },
    ]
    mockFetchWorkspaces.mockResolvedValueOnce({ workspaces })

    await useWorkspaceStore.getState().fetchWorkspaces()

    expect(useWorkspaceStore.getState().workspaces).toEqual(workspaces)
    expect(useWorkspaceStore.getState().loading).toBe(false)
    expect(useWorkspaceStore.getState().error).toBeNull()
  })

  it('fetchWorkspaces sets error on failure', async () => {
    mockFetchWorkspaces.mockRejectedValueOnce(new Error('fetch failed'))

    await useWorkspaceStore.getState().fetchWorkspaces()

    expect(useWorkspaceStore.getState().error).toBe('Error: fetch failed')
    expect(useWorkspaceStore.getState().loading).toBe(false)
  })

  it('createWorkspace adds to list', async () => {
    const ws: WorkspaceRecord = {
      id: 'ws2',
      name: 'New Workspace',
      default_pipeline_key: 'question_content',
      default_entity: 'question',
    }
    mockCreateWorkspace.mockResolvedValueOnce(ws)

    const result = await useWorkspaceStore
      .getState()
      .createWorkspace('New Workspace')

    expect(result).toEqual(ws)
    expect(useWorkspaceStore.getState().workspaces).toContainEqual(ws)
    expect(useWorkspaceStore.getState().error).toBeNull()
  })

  it('createWorkspace sets error on failure', async () => {
    mockCreateWorkspace.mockRejectedValueOnce(new Error('create failed'))

    await expect(
      useWorkspaceStore.getState().createWorkspace('Bad Workspace')
    ).rejects.toThrow('create failed')
    expect(useWorkspaceStore.getState().error).toBe('Error: create failed')
  })

  it('updateWorkspace replaces workspace in list and current selection', async () => {
    const existing: WorkspaceRecord = {
      id: 'ws1',
      name: 'Old',
      default_pipeline_key: 'question_content',
      default_entity: 'question',
    }
    const updated: WorkspaceRecord = {
      id: 'ws1',
      name: 'Old',
      default_pipeline_key: 'question_content',
      default_entity: 'question',
      cms_config: { subject_id: '5' },
    }
    useWorkspaceStore.setState({
      workspaces: [existing],
      currentWorkspace: existing,
    })
    mockUpdateWorkspace.mockResolvedValueOnce(updated)

    const result = await useWorkspaceStore
      .getState()
      .updateWorkspace('ws1', { cms_config: { subject_id: '5' } })

    expect(result).toEqual(updated)
    expect(useWorkspaceStore.getState().workspaces).toEqual([updated])
    expect(useWorkspaceStore.getState().currentWorkspace).toEqual(updated)
  })

  it('deleteWorkspace removes from list', async () => {
    useWorkspaceStore.setState({
      workspaces: [
        {
          id: 'ws1',
          name: 'A',
          default_pipeline_key: 'question_content',
          default_entity: 'question',
        },
        {
          id: 'ws2',
          name: 'B',
          default_pipeline_key: 'question_content',
          default_entity: 'question',
        },
      ],
    })
    mockDeleteWorkspace.mockResolvedValueOnce(undefined)

    await useWorkspaceStore.getState().deleteWorkspace('ws1')

    expect(useWorkspaceStore.getState().workspaces).toHaveLength(1)
    expect(useWorkspaceStore.getState().workspaces[0].id).toBe('ws2')
    expect(useWorkspaceStore.getState().error).toBeNull()
  })

  it('deleteWorkspace sets error on failure', async () => {
    mockDeleteWorkspace.mockRejectedValueOnce(new Error('delete failed'))

    await expect(
      useWorkspaceStore.getState().deleteWorkspace('ws1')
    ).rejects.toThrow('delete failed')
    expect(useWorkspaceStore.getState().error).toBe('Error: delete failed')
  })

  it('setCurrentWorkspace sets current', () => {
    const ws: WorkspaceRecord = {
      id: 'ws1',
      name: 'Current',
      default_pipeline_key: 'question_content',
      default_entity: 'question',
    }
    useWorkspaceStore.getState().setCurrentWorkspace(ws)
    expect(useWorkspaceStore.getState().currentWorkspace).toEqual(ws)
  })

  it('fetchWorkspaceStats sets stats on success', async () => {
    const stats: WorkspaceStats = {
      workspace_id: 'ws1',
      name: 'Test Workspace',
      pipeline_key: 'question_content',
      pipeline_label: 'Question Content',
      job_stats: {
        total_jobs: 10,
        pending: 3,
        running: 2,
        completed: 4,
        failed: 1,
      },
      agent_status: { total: 0, busy: 0, idle: 0 },
      latest_run: null,
    }
    mockFetchWorkspaceStats.mockResolvedValueOnce(stats)

    await useWorkspaceStore.getState().fetchWorkspaceStats('ws1')

    expect(useWorkspaceStore.getState().workspaceStats['ws1']).toEqual(stats)
  })

  it('fetchWorkspaceStats ignores failure', async () => {
    mockFetchWorkspaceStats.mockRejectedValueOnce(new Error('stats failed'))

    await useWorkspaceStore.getState().fetchWorkspaceStats('ws1')

    expect(useWorkspaceStore.getState().workspaceStats['ws1']).toBeUndefined()
  })
})
