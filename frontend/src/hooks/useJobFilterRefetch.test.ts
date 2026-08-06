import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useJobFilterRefetch } from './useJobFilterRefetch'
import { createJobSummary, useJobStore } from '../stores/jobStore'
import * as api from '../api'

vi.mock('../api')

const mockFetchJobsSnapshot = vi.mocked(api.fetchJobsSnapshot)
const mockFetchJobFacets = vi.mocked(api.fetchJobFacets)

const emptyFacets = {
  workspace_id: 'ws1',
  total: 0,
  status_counts: {},
  version_counts: {},
  node_counts: {},
}

describe('useJobFilterRefetch', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useJobStore.setState({
      jobs: [],
      jobsById: {},
      jobIds: [],
      jobIndexById: {},
      filteredJobIds: [],
      revision: 0,
      jobsWorkspaceId: 'ws1',
      isLoading: false,
      error: null,
      nextCursor: null,
      hasMore: false,
      totalJobs: null,
      facets: null,
      loadingMore: false,
      filterConfig: {
        status: null,
        search: '',
        workflowVersion: null,
        activeNodeKey: null,
      },
    })
    mockFetchJobsSnapshot.mockResolvedValue({
      workspace_id: 'ws1',
      revision: 1,
      stats: {},
      total: 1,
      jobs: [createJobSummary({ id: 'j1', status: 'failed' })],
      next_cursor: null,
    })
    mockFetchJobFacets.mockResolvedValue(emptyFacets)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('does not refetch on mount', () => {
    renderHook(() => useJobFilterRefetch('ws1'))

    expect(mockFetchJobsSnapshot).not.toHaveBeenCalled()
  })

  it('refetches the first page immediately when a non-search filter changes', async () => {
    renderHook(() => useJobFilterRefetch('ws1'))

    act(() => {
      useJobStore.getState().setFilterConfig({ status: 'failed' })
    })

    await waitFor(() => {
      expect(mockFetchJobsSnapshot).toHaveBeenCalledWith(
        'ws1',
        500,
        undefined,
        {
          status: 'failed',
          search: null,
          workflow_version: null,
          workflow_version_none: false,
          active_node_key: null,
        }
      )
    })
    await waitFor(() => {
      expect(useJobStore.getState().jobIds).toEqual(['j1'])
    })
  })

  it('debounces search changes by 400ms', async () => {
    vi.useFakeTimers()
    renderHook(() => useJobFilterRefetch('ws1'))

    act(() => {
      useJobStore.getState().setFilterConfig({ search: 'alg' })
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(399)
    })
    expect(mockFetchJobsSnapshot).not.toHaveBeenCalled()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(mockFetchJobsSnapshot).toHaveBeenCalledWith(
      'ws1',
      500,
      undefined,
      expect.objectContaining({ search: 'alg' })
    )
  })

  it('does not refetch when switching workspaces', () => {
    const { rerender } = renderHook(
      ({ workspaceId }) => useJobFilterRefetch(workspaceId),
      { initialProps: { workspaceId: 'ws1' as string | undefined } }
    )

    rerender({ workspaceId: 'ws2' })

    expect(mockFetchJobsSnapshot).not.toHaveBeenCalled()
  })
})
