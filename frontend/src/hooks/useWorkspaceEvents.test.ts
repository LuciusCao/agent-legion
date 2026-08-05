import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { useWorkspaceEvents } from './useWorkspaceEvents'
import { createJobSummary, useJobStore } from '../stores/jobStore'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { EventSourceMock } from '../testing/eventSourceMock'
import * as api from '../api'
import type { WorkspaceStats } from '../types/workspaceTypes'

vi.mock('../api')

const mockFetchJobs = vi.mocked(api.fetchJobs)
const mockFetchJobsSnapshot = vi.mocked(api.fetchJobsSnapshot)
const mockFetchJobFacets = vi.mocked(api.fetchJobFacets)
const mockFetchWorkspaceStats = vi.mocked(api.fetchWorkspaceStats)
const makeJob = createJobSummary

const emptyFacets = {
  workspace_id: 'ws1',
  total: 0,
  status_counts: {},
  version_counts: {},
  node_counts: {},
}

const emptyFilterParams = {
  status: null,
  search: null,
  workflow_version: null,
  workflow_version_none: false,
  active_node_key: null,
}

describe('useWorkspaceEvents', () => {
  const originalEventSource = globalThis.EventSource

  beforeEach(() => {
    EventSourceMock.reset()
    globalThis.EventSource = EventSourceMock as unknown as typeof EventSource
    useJobStore.setState({
      jobs: [],
      jobsById: {},
      jobIds: [],
      revision: 0,
      isLoading: false,
      error: null,
    })
    useWorkspaceStore.setState({ workspaceStats: {} })
    vi.clearAllMocks()
    mockFetchJobs.mockResolvedValue({ jobs: [] })
    mockFetchJobsSnapshot.mockResolvedValue({
      workspace_id: 'ws1',
      revision: 0,
      stats: {},
      jobs: [],
      next_cursor: null,
    })
    mockFetchJobFacets.mockResolvedValue(emptyFacets)
    mockFetchWorkspaceStats.mockResolvedValue({
      job_stats: {},
    } as WorkspaceStats)
  })

  afterEach(() => {
    globalThis.EventSource = originalEventSource
    vi.useRealTimers()
  })

  it('connects to workspace SSE endpoint', async () => {
    renderHook(() => useWorkspaceEvents('ws1'))
    await waitFor(() => {
      expect(EventSourceMock.instances.length).toBe(1)
    })
    expect(EventSourceMock.instances[0].url).toContain(
      '/api/workspaces/ws1/events'
    )
  })

  it('loads jobs snapshot on open', async () => {
    mockFetchJobsSnapshot.mockResolvedValueOnce({
      workspace_id: 'ws1',
      revision: 1,
      stats: { running: 1 },
      jobs: [makeJob({ id: 'j1', workspace_id: 'ws1', status: 'running' })],
      next_cursor: null,
    })

    renderHook(() => useWorkspaceEvents('ws1'))
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onopen?.()
    })

    await waitFor(() => {
      expect(mockFetchJobsSnapshot).toHaveBeenCalledWith(
        'ws1',
        500,
        undefined,
        emptyFilterParams
      )
      expect(useJobStore.getState().jobsById.j1?.status).toBe('running')
    })
    expect(mockFetchJobs).not.toHaveBeenCalled()
  })

  it('receiving a non-heartbeat message triggers refresh', async () => {
    vi.useFakeTimers()
    renderHook(() => useWorkspaceEvents('ws1'))
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onopen?.()
      await vi.advanceTimersByTimeAsync(0)
    })

    await act(async () => {
      source.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({
            type: 'job_updated',
            workspace_id: 'ws1',
            job_id: 'job1',
          }),
        })
      )
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(750)
    })

    // Job list changes arrive via job_patch_batch; legacy job_updated only
    // triggers a stats refresh, never a full jobs refetch.
    expect(mockFetchWorkspaceStats).toHaveBeenCalledWith('ws1')
    expect(mockFetchJobs).not.toHaveBeenCalled()
  })

  it('coalesces rapid job update messages into one stats refresh', async () => {
    vi.useFakeTimers()
    renderHook(() => useWorkspaceEvents('ws1'))
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onopen?.()
      await vi.advanceTimersByTimeAsync(0)
    })

    await act(async () => {
      for (const jobId of ['job1', 'job2', 'job3']) {
        source.onmessage?.(
          new MessageEvent('message', {
            data: JSON.stringify({
              type: 'job_updated',
              workspace_id: 'ws1',
              job_id: jobId,
            }),
          })
        )
      }
    })

    expect(mockFetchWorkspaceStats).not.toHaveBeenCalled()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(750)
    })

    expect(mockFetchWorkspaceStats).toHaveBeenCalledTimes(1)
    expect(mockFetchJobs).not.toHaveBeenCalled()
  })

  it('closes the EventSource on cleanup', () => {
    const { unmount } = renderHook(() => useWorkspaceEvents('ws1'))
    const source = EventSourceMock.instances[0]

    unmount()

    expect(source.close).toHaveBeenCalled()
  })

  it('resets jobs and sets loading when workspaceId changes', () => {
    useJobStore.setState({
      jobs: [createJobSummary({ id: 'j1', workspace_id: 'ws1' })],
      jobsById: { j1: createJobSummary({ id: 'j1', workspace_id: 'ws1' }) },
      jobIds: ['j1'],
      isLoading: false,
      jobsWorkspaceId: 'ws1',
    })

    renderHook(() => useWorkspaceEvents('ws2'))

    expect(useJobStore.getState().jobs).toEqual([])
    expect(useJobStore.getState().isLoading).toBe(true)
    expect(useJobStore.getState().jobsWorkspaceId).toBe('ws2')
  })

  it('clears loading after SSE opens and jobs are fetched', async () => {
    mockFetchJobsSnapshot.mockResolvedValueOnce({
      workspace_id: 'ws1',
      revision: 1,
      stats: {},
      jobs: [makeJob({ id: 'j2', workspace_id: 'ws1' })],
      next_cursor: null,
    })

    renderHook(() => useWorkspaceEvents('ws1'))
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onopen?.()
    })

    await waitFor(() => {
      expect(useJobStore.getState().isLoading).toBe(false)
      expect(useJobStore.getState().jobs).toHaveLength(1)
    })
  })

  it('clears a previous error after successful refresh', async () => {
    useJobStore.setState({ error: 'previous error' })

    renderHook(() => useWorkspaceEvents('ws1'))
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onopen?.()
    })

    await waitFor(() => {
      expect(useJobStore.getState().error).toBeNull()
    })
  })

  it('does not connect when enabled is false', () => {
    renderHook(() => useWorkspaceEvents('ws1', false))
    expect(EventSourceMock.instances.length).toBe(0)
  })

  it('does not connect when workspaceId is undefined', () => {
    renderHook(() => useWorkspaceEvents(undefined))
    expect(EventSourceMock.instances.length).toBe(0)
  })

  it('statsOnly fetches workspace stats but not jobs on refresh', async () => {
    vi.useFakeTimers()
    renderHook(() => useWorkspaceEvents('ws1', true, true))
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({
            type: 'job_updated',
            workspace_id: 'ws1',
            job_id: 'job1',
          }),
        })
      )
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(750)
    })

    expect(mockFetchWorkspaceStats).toHaveBeenCalledWith('ws1')
    expect(mockFetchJobs).not.toHaveBeenCalled()
  })

  it('updates workspace stats from payload stats', async () => {
    mockFetchWorkspaceStats.mockResolvedValue({
      job_stats: { running: 2, completed: 5 },
    } as unknown as WorkspaceStats)
    renderHook(() => useWorkspaceEvents('ws1'))
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onopen?.()
    })
    await waitFor(() => {
      expect(mockFetchJobsSnapshot).toHaveBeenCalled()
    })

    await act(async () => {
      source.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({
            type: 'stats_updated',
            workspace_id: 'ws1',
            stats: { running: 2, completed: 5 },
          }),
        })
      )
    })

    await waitFor(() => {
      expect(useWorkspaceStore.getState().workspaceStats.ws1).toEqual({
        job_stats: { running: 2, completed: 5 },
      })
    })
  })

  it('ignores messages for other workspaces', async () => {
    renderHook(() => useWorkspaceEvents('ws1'))
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onopen?.()
    })
    await waitFor(() => {
      expect(mockFetchJobsSnapshot).toHaveBeenCalled()
    })

    await act(async () => {
      source.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({
            type: 'job_updated',
            workspace_id: 'ws2',
            job_id: 'job1',
          }),
        })
      )
    })

    await act(async () => new Promise((resolve) => setTimeout(resolve, 50)))
    expect(mockFetchWorkspaceStats).not.toHaveBeenCalled()
  })

  it('ignores heartbeat messages', async () => {
    renderHook(() => useWorkspaceEvents('ws1'))
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onopen?.()
    })
    await waitFor(() => {
      expect(mockFetchJobsSnapshot).toHaveBeenCalled()
    })

    await act(async () => {
      source.onmessage?.(
        new MessageEvent('message', {
          data: ':heartbeat',
        })
      )
    })

    await act(async () => new Promise((resolve) => setTimeout(resolve, 50)))
    expect(mockFetchWorkspaceStats).not.toHaveBeenCalled()
  })

  it('reconnects after an error', async () => {
    vi.useFakeTimers()
    renderHook(() => useWorkspaceEvents('ws1'))
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onerror?.()
    })

    await act(async () => {
      vi.advanceTimersByTime(1100)
    })

    expect(EventSourceMock.instances.length).toBe(2)
    vi.useRealTimers()
  })

  it('applies job patch batch without fetching all jobs', async () => {
    renderHook(() => useWorkspaceEvents('ws1'))
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onopen?.()
    })
    await waitFor(() => {
      expect(mockFetchJobsSnapshot).toHaveBeenCalled()
    })

    source.emitMessage({
      type: 'job_patch_batch',
      workspace_id: 'ws1',
      revision: 2,
      stats: { running: 1 },
      jobs: [makeJob({ id: 'j1', workspace_id: 'ws1', status: 'running' })],
      deleted_job_ids: [],
    })

    await waitFor(() => {
      expect(useJobStore.getState().jobsById.j1.status).toBe('running')
    })
    expect(mockFetchJobs).not.toHaveBeenCalled()
  })

  it('resyncs instead of dropping events when the pending queue overflows', async () => {
    let snapshotCalls = 0
    mockFetchJobsSnapshot.mockImplementation(() => {
      snapshotCalls += 1
      if (snapshotCalls === 1) {
        // First snapshot load hangs so events keep queuing behind it.
        return new Promise(() => {})
      }
      return Promise.resolve({
        workspace_id: 'ws1',
        revision: 5,
        stats: {},
        jobs: [],
        next_cursor: null,
      })
    })

    renderHook(() => useWorkspaceEvents('ws1'))
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onopen?.()
    })
    await waitFor(() => {
      expect(mockFetchJobsSnapshot).toHaveBeenCalledTimes(1)
    })

    await act(async () => {
      for (let i = 0; i < 1001; i += 1) {
        source.onmessage?.(
          new MessageEvent('message', {
            data: JSON.stringify({
              type: 'job_updated',
              workspace_id: 'ws1',
              job_id: `job${i}`,
            }),
          })
        )
      }
    })

    await waitFor(() => {
      expect(mockFetchJobsSnapshot).toHaveBeenCalledTimes(2)
    })
  })

  it('resyncs snapshot when backend requests resync', async () => {
    mockFetchJobsSnapshot.mockResolvedValueOnce({
      workspace_id: 'ws1',
      revision: 10,
      stats: { completed: 1 },
      jobs: [makeJob({ id: 'j2', workspace_id: 'ws1', status: 'completed' })],
      next_cursor: null,
    })
    renderHook(() => useWorkspaceEvents('ws1'))
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onopen?.()
    })
    await waitFor(() => {
      expect(mockFetchJobsSnapshot).toHaveBeenCalled()
    })

    source.emitMessage({
      type: 'resync_required',
      workspace_id: 'ws1',
      latest_revision: 10,
      reason: 'event_buffer_overflow',
    })

    await waitFor(() => {
      expect(useJobStore.getState().jobsById.j2.status).toBe('completed')
    })
  })

  it('loads only the first snapshot page and fetches facets', async () => {
    mockFetchJobsSnapshot.mockResolvedValueOnce({
      workspace_id: 'ws1',
      revision: 3,
      stats: { running: 1 },
      total: 900,
      jobs: [makeJob({ id: 'j1', workspace_id: 'ws1', status: 'running' })],
      next_cursor: 'cursor-page-2',
    })

    renderHook(() => useWorkspaceEvents('ws1'))
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onopen?.()
    })

    await waitFor(() => {
      expect(useJobStore.getState().isLoading).toBe(false)
    })
    // No next_cursor loop: the remaining pages load via infinite scroll.
    expect(mockFetchJobsSnapshot).toHaveBeenCalledTimes(1)
    expect(mockFetchJobFacets).toHaveBeenCalledWith('ws1', emptyFilterParams)
    const state = useJobStore.getState()
    expect(state.hasMore).toBe(true)
    expect(state.nextCursor).toBe('cursor-page-2')
    expect(state.totalJobs).toBe(900)
  })

  it('debounces a facet refresh after a job patch batch', async () => {
    vi.useFakeTimers()
    renderHook(() => useWorkspaceEvents('ws1'))
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onopen?.()
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(mockFetchJobFacets).toHaveBeenCalledTimes(1)

    await act(async () => {
      source.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({
            type: 'job_patch_batch',
            workspace_id: 'ws1',
            revision: 2,
            jobs: [makeJob({ id: 'j1', workspace_id: 'ws1' })],
            deleted_job_ids: [],
          }),
        })
      )
    })
    expect(mockFetchJobFacets).toHaveBeenCalledTimes(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(750)
    })
    expect(mockFetchJobFacets).toHaveBeenCalledTimes(2)
  })
})
