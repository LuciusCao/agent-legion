import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useJobStore } from '../index'
import { useWorkspaceStore } from '../../workspaceStore'
import { fetchJobFacets } from '../../../api'
import type { JobFacetsResponse } from '../../../types/jobTypes'
import type { WorkspaceStats } from '../../../types/workspaceTypes'
import { createJobSummary } from './testHelpers'
import { normalizeJobs } from './fetchStateHelpers'

vi.mock('../../../api', () => ({
  fetchJobFacets: vi.fn(),
  fetchJobsSnapshot: vi.fn(),
}))

const mockFetchJobFacets = vi.mocked(fetchJobFacets)

const emptyFacets: JobFacetsResponse = {
  workspace_id: 'ws1',
  total: 0,
  status_counts: {},
  version_counts: {},
  node_counts: {},
}

function resetSelection() {
  useJobStore.setState({
    ...normalizeJobs([]),
    jobsWorkspaceId: 'ws1',
    totalJobs: null,
    selectedIds: new Set(),
    selectionMode: 'explicit',
    selectionFilter: null,
    excludedIds: new Set(),
    selectionCount: null,
    filterConfig: {
      status: null,
      search: '',
      workflowVersion: null,
      activeNodeKey: null,
    },
  })
}

describe('selectionModeActions', () => {
  beforeEach(() => {
    resetSelection()
    useWorkspaceStore.setState({ workspaceStats: {} })
    mockFetchJobFacets.mockReset()
    mockFetchJobFacets.mockResolvedValue(emptyFacets)
  })

  it('selectAll enters allMatching mode with the current filter', () => {
    useJobStore.setState({
      totalJobs: 42,
      filterConfig: {
        status: 'failed',
        search: 'abc',
        workflowVersion: null,
        activeNodeKey: null,
      },
    })

    useJobStore.getState().selectAll()

    const state = useJobStore.getState()
    expect(state.selectionMode).toBe('allMatching')
    expect(state.selectionFilter).toEqual({
      status: 'failed',
      search: 'abc',
      workflow_version: null,
      workflow_version_none: false,
      active_node_key: null,
    })
    expect(state.selectionCount).toBe(42)
    expect(state.selectedIds).toEqual(new Set())
    expect(state.excludedIds).toEqual(new Set())
  })

  it('selectAll maps the none workflow version filter', () => {
    useJobStore.setState({
      filterConfig: {
        status: null,
        search: '',
        workflowVersion: 'none',
        activeNodeKey: null,
      },
    })

    useJobStore.getState().selectAll()

    expect(useJobStore.getState().selectionFilter).toMatchObject({
      workflow_version_none: true,
    })
  })

  it('selectFailed enters allMatching mode with a failed-only filter', () => {
    useWorkspaceStore.setState({
      workspaceStats: {
        ws1: { job_stats: { failed: 7 } } as unknown as WorkspaceStats,
      },
    })

    useJobStore.getState().selectFailed()

    const state = useJobStore.getState()
    expect(state.selectionMode).toBe('allMatching')
    expect(state.selectionFilter).toEqual({
      status: 'failed',
      search: null,
      workflow_version: null,
      workflow_version_none: false,
      active_node_key: null,
    })
    expect(state.selectionCount).toBe(7)
  })

  it('selectFailed ignores the active list filter', () => {
    useJobStore.setState({
      filterConfig: {
        status: 'completed',
        search: 'abc',
        workflowVersion: null,
        activeNodeKey: null,
      },
    })

    useJobStore.getState().selectFailed()

    expect(useJobStore.getState().selectionFilter).toMatchObject({
      status: 'failed',
      search: null,
    })
  })

  it('selectUnpacked enters allMatching mode and resolves the count via facets', async () => {
    mockFetchJobFacets.mockResolvedValue({ ...emptyFacets, total: 5 })

    useJobStore.getState().selectUnpacked()

    const state = useJobStore.getState()
    expect(state.selectionMode).toBe('allMatching')
    expect(state.selectionFilter).toEqual({
      status: 'completed',
      search: null,
      workflow_version: null,
      workflow_version_none: false,
      active_node_key: null,
      packed: 0,
    })
    await vi.waitFor(() => {
      expect(useJobStore.getState().selectionCount).toBe(5)
    })
    expect(mockFetchJobFacets).toHaveBeenCalledWith('ws1', {
      status: 'completed',
      search: null,
      workflow_version: null,
      workflow_version_none: false,
      active_node_key: null,
      packed: 0,
    })
  })

  it('toggleSelect moves rows in and out of excludedIds in allMatching mode', () => {
    useJobStore.getState().selectAll()

    useJobStore.getState().toggleSelect('j1')
    expect(useJobStore.getState().excludedIds).toEqual(new Set(['j1']))
    expect(useJobStore.getState().selectedIds).toEqual(new Set())

    useJobStore.getState().toggleSelect('j1')
    expect(useJobStore.getState().excludedIds).toEqual(new Set())
  })

  it('toggleSelect keeps explicit id behavior in explicit mode', () => {
    useJobStore.getState().toggleSelect('j1')
    expect(useJobStore.getState().selectionMode).toBe('explicit')
    expect(useJobStore.getState().selectedIds).toEqual(new Set(['j1']))
  })

  it('clearSelection resets to an empty explicit selection', () => {
    useJobStore.getState().selectAll()
    useJobStore.getState().toggleSelect('j1')

    useJobStore.getState().clearSelection()

    const state = useJobStore.getState()
    expect(state.selectionMode).toBe('explicit')
    expect(state.selectionFilter).toBeNull()
    expect(state.excludedIds).toEqual(new Set())
    expect(state.selectedIds).toEqual(new Set())
    expect(state.selectionCount).toBeNull()
  })

  it('changing the filter config clears an allMatching selection', () => {
    useJobStore.getState().selectAll()

    useJobStore.getState().setFilterConfig({ status: 'failed' })

    const state = useJobStore.getState()
    expect(state.selectionMode).toBe('explicit')
    expect(state.selectionFilter).toBeNull()
  })
})

describe('selectionActions legacy explicit behavior', () => {
  beforeEach(() => {
    resetSelection()
  })

  it('selectUnpacked no longer depends on loaded jobs', () => {
    useJobStore.setState(
      normalizeJobs([
        createJobSummary({ id: 'j1', status: 'completed', packed: 0 }),
      ])
    )

    useJobStore.getState().selectUnpacked()

    const state = useJobStore.getState()
    expect(state.selectionMode).toBe('allMatching')
    expect(state.selectedIds).toEqual(new Set())
  })
})
