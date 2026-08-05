import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createElement, type ReactNode } from 'react'
import { renderHook, act } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { useDashboardEvents } from './useDashboardEvents'
import { EventSourceMock } from '../testing/eventSourceMock'
import { createTestQueryClient } from '../testing/testQueryClient'
import { queryKeys } from '../lib/queryKeys'

describe('useDashboardEvents', () => {
  const originalEventSource = globalThis.EventSource
  let testClient = createTestQueryClient()

  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: testClient }, children)
  const renderEvents = () => renderHook(() => useDashboardEvents(), { wrapper })

  beforeEach(() => {
    testClient = createTestQueryClient()
    EventSourceMock.reset()
    globalThis.EventSource = EventSourceMock as unknown as typeof EventSource
  })

  afterEach(() => {
    globalThis.EventSource = originalEventSource
    vi.useRealTimers()
  })

  it('writes workspace_stats_batch into the query cache', () => {
    renderEvents()
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

    expect(testClient.getQueryData(queryKeys.workspaceStats('ws1'))).toEqual({
      job_stats: { running: 2 },
    })
  })

  it('ignores heartbeat messages', () => {
    renderEvents()
    const source = EventSourceMock.instances[0]

    act(() => {
      source.onmessage?.(new MessageEvent('message', { data: ':heartbeat' }))
    })

    expect(
      testClient.getQueryData(queryKeys.workspaceStats('ws1'))
    ).toBeUndefined()
  })

  it('reconnects with backoff after an error', () => {
    vi.useFakeTimers()
    renderEvents()
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
    const { unmount } = renderEvents()
    const source = EventSourceMock.instances[0]

    unmount()

    expect(source.close).toHaveBeenCalled()
  })
})
