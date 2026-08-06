import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useJobListLoadMore } from './useJobListLoadMore'
import { createJobSummary, useJobStore } from '../stores/jobStore'
import * as api from '../api'

vi.mock('../api')

const mockFetchJobsSnapshot = vi.mocked(api.fetchJobsSnapshot)

function seedLoadedPage(nextCursor: string | null) {
  const job = createJobSummary({ id: 'j1', workspace_id: 'ws1' })
  useJobStore.setState({
    jobs: [job],
    jobsById: { j1: job },
    jobIds: ['j1'],
    jobIndexById: { j1: 0 },
    filteredJobIds: ['j1'],
    jobsWorkspaceId: 'ws1',
    revision: 1,
    isLoading: false,
    nextCursor,
    hasMore: nextCursor !== null,
    totalJobs: 2,
    loadingMore: false,
    facets: null,
    filterConfig: {
      status: null,
      search: '',
      workflowVersion: null,
      activeNodeKey: null,
    },
  })
}

describe('useJobListLoadMore', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    seedLoadedPage('cursor-1')
  })

  it('loads the next page when the end of the list is near', async () => {
    mockFetchJobsSnapshot.mockResolvedValueOnce({
      workspace_id: 'ws1',
      revision: 1,
      stats: {},
      jobs: [createJobSummary({ id: 'j2', workspace_id: 'ws1' })],
      next_cursor: null,
    })

    renderHook(() => useJobListLoadMore('ws1', 8, 5))

    await waitFor(() => {
      expect(mockFetchJobsSnapshot).toHaveBeenCalledWith(
        'ws1',
        500,
        'cursor-1',
        expect.objectContaining({ status: null })
      )
    })
    await waitFor(() => {
      expect(useJobStore.getState().jobIds).toEqual(['j1', 'j2'])
      expect(useJobStore.getState().hasMore).toBe(false)
    })
  })

  it('does not load when far from the end', () => {
    renderHook(() => useJobListLoadMore('ws1', 100, 5))

    expect(mockFetchJobsSnapshot).not.toHaveBeenCalled()
  })

  it('does not load when there are no more pages', () => {
    seedLoadedPage(null)

    renderHook(() => useJobListLoadMore('ws1', 8, 7))

    expect(mockFetchJobsSnapshot).not.toHaveBeenCalled()
  })

  it('attempts each cursor only once, even after a failure', async () => {
    mockFetchJobsSnapshot.mockRejectedValue(new Error('boom'))

    const { rerender } = renderHook(
      ({ lastIndex }) => useJobListLoadMore('ws1', 8, lastIndex),
      { initialProps: { lastIndex: 5 } }
    )

    await waitFor(() => {
      expect(mockFetchJobsSnapshot).toHaveBeenCalledTimes(1)
    })
    await waitFor(() => {
      expect(useJobStore.getState().loadingMore).toBe(false)
    })

    rerender({ lastIndex: 7 })

    expect(mockFetchJobsSnapshot).toHaveBeenCalledTimes(1)
    expect(useJobStore.getState().hasMore).toBe(true)
  })
})
