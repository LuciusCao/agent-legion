import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createRealtimeChannel } from './realtime'
import { SSE_STALL_TIMEOUT_MULTIPLIER } from './sseStallWatchdog'
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

  it('ws error closes the socket so reconnect can proceed via onclose', () => {
    createRealtimeChannel({
      url: 'ws://example/socket',
      protocol: 'ws',
      onEvent: vi.fn(),
    })

    WebSocketMock.instances[0].onerror?.()
    expect(WebSocketMock.instances[0].close).toHaveBeenCalledTimes(1)

    WebSocketMock.instances[0].onclose?.()
    vi.advanceTimersByTime(1000)
    expect(WebSocketMock.instances.length).toBe(2)
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

  describe('SSE heartbeat watchdog (#914)', () => {
    const interval = 1000
    const timeout = interval * SSE_STALL_TIMEOUT_MULTIPLIER

    function openSse(onStatus = vi.fn(), onEvent = vi.fn()) {
      const channel = createRealtimeChannel({
        url: '/api/events',
        protocol: 'sse',
        onEvent,
        onStatus,
      })
      const source = EventSourceMock.instances[0]
      source.onopen?.()
      source.emitHeartbeat(interval)
      return { channel, source, onStatus, onEvent }
    }

    it('silent stall past N heartbeats → reconnecting, then recovers', () => {
      const { channel, source, onStatus } = openSse()
      expect(channel.status()).toBe('open')

      vi.advanceTimersByTime(timeout - 1)
      expect(source.close).not.toHaveBeenCalled()
      vi.advanceTimersByTime(1)
      // Treated like `error`: instance closed, reconnect after backoff.
      expect(source.close).toHaveBeenCalledTimes(1)
      vi.advanceTimersByTime(1000)
      expect(EventSourceMock.instances).toHaveLength(2)
      expect(onStatus).toHaveBeenLastCalledWith('connecting')

      const next = EventSourceMock.instances[1]
      next.onopen?.()
      expect(channel.status()).toBe('open')
      // The learned interval survives the reconnect: open alone re-arms.
      vi.advanceTimersByTime(timeout)
      expect(next.close).toHaveBeenCalledTimes(1)
      channel.close()
    })

    it('regular heartbeats never trip the watchdog', () => {
      const { channel, source, onStatus } = openSse()
      for (let i = 0; i < 20; i += 1) {
        vi.advanceTimersByTime(interval)
        source.emitHeartbeat(interval)
      }
      expect(source.close).not.toHaveBeenCalled()
      expect(EventSourceMock.instances).toHaveLength(1)
      expect(onStatus).toHaveBeenLastCalledWith('open')
      channel.close()
    })

    it('data events also count as liveness', () => {
      const { channel, source } = openSse()
      for (let i = 0; i < 10; i += 1) {
        vi.advanceTimersByTime(timeout - 100)
        source.emitMessage({ type: 'job_updated' })
      }
      expect(source.close).not.toHaveBeenCalled()
      channel.close()
    })

    it('heartbeat is not forwarded to onEvent', () => {
      const { channel, onEvent } = openSse()
      expect(onEvent).not.toHaveBeenCalled()
      channel.close()
    })

    it('stays disarmed until the server advertises a heartbeat', () => {
      createRealtimeChannel({
        url: '/api/events',
        protocol: 'sse',
        onEvent: vi.fn(),
      })
      const source = EventSourceMock.instances[0]
      source.onopen?.()
      source.emitNamed('heartbeat', 'not-json')
      vi.advanceTimersByTime(10 * 60_000)
      expect(source.close).not.toHaveBeenCalled()
    })

    it('close() cancels the watchdog', () => {
      const { channel } = openSse()
      channel.close()
      vi.advanceTimersByTime(timeout * 4)
      expect(EventSourceMock.instances).toHaveLength(1)
      expect(channel.status()).toBe('closed')
    })
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
