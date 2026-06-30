import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { useWorkspaceEvents } from './useWorkspaceEvents'
import { useJobStore } from '../stores/jobStore'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { EventSourceMock } from '../testing/eventSourceMock'
import * as api from '../api'
import type { WorkspaceStats } from '../workspaceTypes'
import { createJobSummary } from '../stores/job/actions/testHelpers'

vi.mock('../api')

const mockFetchJobs = vi.mocked(api.fetchJobs)
const mockFetchWorkspaceStats = vi.mocked(api.fetchWorkspaceStats)

describe('useWorkspaceEvents', () => {
  const originalEventSource = globalThis.EventSource

  beforeEach(() => {
    EventSourceMock.reset()
    globalThis.EventSource = EventSourceMock as unknown as typeof EventSource
    useJobStore.setState({ jobs: [] })
    useWorkspaceStore.setState({ workspaceStats: {} })
    vi.clearAllMocks()
    mockFetchJobs.mockResolvedValue({ jobs: [] })
    mockFetchWorkspaceStats.mockResolvedValue({
      job_stats: {},
    } as WorkspaceStats)
  })

  afterEach(() => {
    globalThis.EventSource = originalEventSource
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

  it('refreshes stats and jobs on open', async () => {
    renderHook(() => useWorkspaceEvents('ws1'))
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onopen?.()
    })

    await waitFor(() => {
      expect(mockFetchWorkspaceStats).toHaveBeenCalledWith('ws1')
      expect(mockFetchJobs).toHaveBeenCalledWith('ws1')
    })
  })

  it('receiving a non-heartbeat message triggers refresh', async () => {
    renderHook(() => useWorkspaceEvents('ws1'))
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

    await waitFor(() => {
      expect(mockFetchJobs).toHaveBeenCalledWith('ws1')
      expect(mockFetchWorkspaceStats).toHaveBeenCalledWith('ws1')
    })
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
      isLoading: false,
      jobsWorkspaceId: 'ws1',
    })

    renderHook(() => useWorkspaceEvents('ws2'))

    expect(useJobStore.getState().jobs).toEqual([])
    expect(useJobStore.getState().isLoading).toBe(true)
    expect(useJobStore.getState().jobsWorkspaceId).toBe('ws2')
  })

  it('clears loading after SSE opens and jobs are fetched', async () => {
    mockFetchJobs.mockResolvedValue({
      jobs: [createJobSummary({ id: 'j2', workspace_id: 'ws1' })],
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
    mockFetchJobs.mockResolvedValue({ jobs: [] })

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

    await waitFor(() => {
      expect(mockFetchWorkspaceStats).toHaveBeenCalledWith('ws1')
    })
    expect(mockFetchJobs).not.toHaveBeenCalled()
  })

  it('updates workspace stats from payload stats', async () => {
    mockFetchWorkspaceStats.mockResolvedValue({
      job_stats: { running: 2, completed: 5 },
    } as unknown as WorkspaceStats)
    renderHook(() => useWorkspaceEvents('ws1'))
    const source = EventSourceMock.instances[0]

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
})
