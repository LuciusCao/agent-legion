import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createRealtimeChannel } from './realtime'
import { WebSocketMock } from '../testing/webSocketMock'
import { EventSourceMock } from '../testing/eventSourceMock'

describe('createRealtimeChannel', () => {
  const originalWebSocket = globalThis.WebSocket
  const originalEventSource = globalThis.EventSource

  beforeEach(() => {
    vi.useFakeTimers()
    WebSocketMock.reset()
    EventSourceMock.reset()
    globalThis.WebSocket = WebSocketMock as unknown as typeof WebSocket
    globalThis.EventSource = EventSourceMock as unknown as typeof EventSource
  })

  afterEach(() => {
    globalThis.WebSocket = originalWebSocket
    globalThis.EventSource = originalEventSource
    vi.useRealTimers()
  })

  it('reports connecting then open status', () => {
    const onStatus = vi.fn()
    createRealtimeChannel({
      url: 'ws://example/socket',
      protocol: 'ws',
      onEvent: vi.fn(),
      onStatus,
    })
    expect(onStatus).toHaveBeenLastCalledWith('connecting')

    WebSocketMock.instances[0].onopen?.()
    expect(onStatus).toHaveBeenLastCalledWith('open')
  })

  it('ws message triggers onEvent(null, rawData)', () => {
    const onEvent = vi.fn()
    createRealtimeChannel({
      url: 'ws://example/socket',
      protocol: 'ws',
      onEvent,
    })

    WebSocketMock.instances[0].onmessage?.(
      new MessageEvent('message', { data: '{"a":1}' })
    )
    expect(onEvent).toHaveBeenCalledWith(null, '{"a":1}')
  })

  it('sse message triggers onEvent(null, data)', () => {
    const onEvent = vi.fn()
    createRealtimeChannel({
      url: '/api/events',
      protocol: 'sse',
      onEvent,
    })

    EventSourceMock.instances[0].onmessage?.(
      new MessageEvent('message', { data: 'payload' })
    )
    expect(onEvent).toHaveBeenCalledWith(null, 'payload')
  })

  it('reconnects with exponential backoff capped at maxDelay', () => {
    createRealtimeChannel({
      url: 'ws://example/socket',
      protocol: 'ws',
      onEvent: vi.fn(),
    })
    expect(WebSocketMock.instances.length).toBe(1)

    WebSocketMock.instances[0].onclose?.()
    vi.advanceTimersByTime(999)
    expect(WebSocketMock.instances.length).toBe(1)
    vi.advanceTimersByTime(1)
    expect(WebSocketMock.instances.length).toBe(2)

    WebSocketMock.instances[1].onclose?.()
    vi.advanceTimersByTime(2000)
    expect(WebSocketMock.instances.length).toBe(3)

    WebSocketMock.instances[2].onclose?.()
    vi.advanceTimersByTime(4000)
    expect(WebSocketMock.instances.length).toBe(4)

    WebSocketMock.instances[3].onclose?.()
    vi.advanceTimersByTime(8000)
    expect(WebSocketMock.instances.length).toBe(5)

    WebSocketMock.instances[4].onclose?.()
    vi.advanceTimersByTime(16000)
    expect(WebSocketMock.instances.length).toBe(6)

    WebSocketMock.instances[5].onclose?.()
    vi.advanceTimersByTime(30000)
    expect(WebSocketMock.instances.length).toBe(7)

    // capped at 30000: the next wait is 30000 again, not 32000
    WebSocketMock.instances[6].onclose?.()
    vi.advanceTimersByTime(29999)
    expect(WebSocketMock.instances.length).toBe(7)
    vi.advanceTimersByTime(1)
    expect(WebSocketMock.instances.length).toBe(8)
  })

  it('resets backoff to minDelay after a successful open', () => {
    createRealtimeChannel({
      url: 'ws://example/socket',
      protocol: 'ws',
      onEvent: vi.fn(),
    })

    WebSocketMock.instances[0].onclose?.()
    vi.advanceTimersByTime(1000)
    expect(WebSocketMock.instances.length).toBe(2)

    WebSocketMock.instances[1].onopen?.()
    WebSocketMock.instances[1].onclose?.()
    vi.advanceTimersByTime(1000)
    expect(WebSocketMock.instances.length).toBe(3)
  })

  it('does not reconnect after close()', () => {
    const channel = createRealtimeChannel({
      url: 'ws://example/socket',
      protocol: 'ws',
      onEvent: vi.fn(),
    })
    WebSocketMock.instances[0].onclose?.()

    channel.close()

    vi.advanceTimersByTime(120000)
    expect(WebSocketMock.instances.length).toBe(1)
    expect(channel.status()).toBe('closed')
  })

  it('close() is idempotent', () => {
    const channel = createRealtimeChannel({
      url: 'ws://example/socket',
      protocol: 'ws',
      onEvent: vi.fn(),
    })

    channel.close()
    channel.close()

    expect(WebSocketMock.instances[0].close).toHaveBeenCalledTimes(1)
    expect(channel.status()).toBe('closed')
  })
})
