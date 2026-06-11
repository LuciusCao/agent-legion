import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useVideoEvents } from './useVideoEvents'
import { EventSourceMock } from '../testing/eventSourceMock'
import * as api from '../api'

vi.mock('../api')
vi.mock('../lib/download')

const mockFetchPackages = vi.mocked(api.fetchPackages)

describe('useVideoEvents', () => {
  const originalEventSource = globalThis.EventSource

  beforeEach(() => {
    EventSourceMock.reset()
    globalThis.EventSource = EventSourceMock as unknown as typeof EventSource
    vi.clearAllMocks()
    mockFetchPackages.mockResolvedValue({ packages: [] })
  })

  afterEach(() => {
    globalThis.EventSource = originalEventSource
    vi.useRealTimers()
  })

  it('returns empty events initially', () => {
    const { result } = renderHook(() => useVideoEvents())
    expect(result.current.events).toEqual([])
  })

  it('adds incoming SSE messages to events', async () => {
    const { result } = renderHook(() => useVideoEvents())
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({ type: 'video_updated', video: { id: 'v1' } }),
        })
      )
    })

    expect(result.current.events).toHaveLength(1)
    expect(result.current.events[0].type).toBe('video_updated')
  })

  it('reconnects after an error with exponential backoff', () => {
    vi.useFakeTimers()
    renderHook(() => useVideoEvents())

    const first = EventSourceMock.instances[0]
    act(() => {
      first.onerror?.()
    })

    expect(first.close).toHaveBeenCalled()
    vi.advanceTimersByTime(1100)
    expect(EventSourceMock.instances.length).toBe(2)
  })

  it('closes the EventSource on unmount', () => {
    const { unmount } = renderHook(() => useVideoEvents())
    const source = EventSourceMock.instances[0]

    unmount()

    expect(source.close).toHaveBeenCalled()
  })
})
