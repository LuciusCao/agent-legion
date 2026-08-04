import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useDashboardEvents } from './useDashboardEvents'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { EventSourceMock } from '../testing/eventSourceMock'

describe('useDashboardEvents', () => {
  const originalEventSource = globalThis.EventSource

  beforeEach(() => {
    EventSourceMock.reset()
    globalThis.EventSource = EventSourceMock as unknown as typeof EventSource
    useWorkspaceStore.setState({ workspaceStats: {} })
  })

  afterEach(() => {
    globalThis.EventSource = originalEventSource
    vi.useRealTimers()
  })

  it('dispatches workspace_stats_batch to the workspace store', () => {
    renderHook(() => useDashboardEvents())
    const source = EventSourceMock.instances[0]

    act(() => {
      source.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({
            type: 'workspace_stats_batch',
            workspaces: [{ id: 'ws1', job_stats: { running: 2 } }],
          }),
        })
      )
    })

    expect(useWorkspaceStore.getState().workspaceStats.ws1).toEqual({
      job_stats: { running: 2 },
    })
  })

  it('ignores heartbeat messages', () => {
    renderHook(() => useDashboardEvents())
    const source = EventSourceMock.instances[0]

    act(() => {
      source.onmessage?.(new MessageEvent('message', { data: ':heartbeat' }))
    })

    expect(useWorkspaceStore.getState().workspaceStats).toEqual({})
  })

  it('reconnects with backoff after an error', () => {
    vi.useFakeTimers()
    renderHook(() => useDashboardEvents())
    expect(EventSourceMock.instances.length).toBe(1)

    act(() => {
      EventSourceMock.instances[0].onerror?.()
    })
    act(() => {
      vi.advanceTimersByTime(1000)
    })

    expect(EventSourceMock.instances.length).toBe(2)
  })

  it('closes the channel on unmount', () => {
    const { unmount } = renderHook(() => useDashboardEvents())
    const source = EventSourceMock.instances[0]

    unmount()

    expect(source.close).toHaveBeenCalled()
  })
})
