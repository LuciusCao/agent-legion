import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { useWorkspaceEvents } from './useWorkspaceEvents'
import { useJobStore } from '../stores/jobStore'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { EventSourceMock } from '../testing/eventSourceMock'
import * as api from '../api'
import type { WorkspaceStats } from '../workspaceTypes'

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
})
