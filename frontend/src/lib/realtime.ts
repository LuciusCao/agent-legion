import { createSseStallWatchdog, SSE_HEARTBEAT_EVENT } from './sseStallWatchdog'

export type ConnectionStatus = 'connecting' | 'open' | 'closed'

export interface RealtimeChannelOptions {
  url: string
  protocol: 'sse' | 'ws'
  onEvent: (type: string | null, data: string) => void
  onStatus?: (status: ConnectionStatus) => void
  minDelayMs?: number
  maxDelayMs?: number
}

export interface RealtimeChannel {
  close: () => void
  status: () => ConnectionStatus
}

/**
 * Unified realtime connection layer for the app's WS/SSE channels.
 *
 * Reconnects with exponential backoff after failure/close (starting at
 * minDelayMs, doubling up to maxDelayMs, reset on open). close() is
 * idempotent, clears the pending reconnect timer and prevents reconnects.
 *
 * SSE also runs a stall watchdog (#914, see sseStallWatchdog): a silently
 * hung stream never fires `error`, so no event (heartbeat included) for a
 * few server-advertised heartbeat intervals takes the same exit as `error`.
 */
export function createRealtimeChannel(
  opts: RealtimeChannelOptions
): RealtimeChannel {
  const { url, protocol, onEvent, onStatus } = opts
  const minDelay = opts.minDelayMs ?? 1000
  const maxDelay = opts.maxDelayMs ?? 30000

  let currentStatus: ConnectionStatus = 'connecting'
  let closed = false
  let delay = minDelay
  let timer: ReturnType<typeof setTimeout> | null = null
  let ws: WebSocket | null = null
  let source: EventSource | null = null
  const watchdog = createSseStallWatchdog()

  const setStatus = (status: ConnectionStatus) => {
    currentStatus = status
    onStatus?.(status)
  }

  const clearTimer = () => {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  const detachInstance = () => {
    if (ws) {
      ws.onopen = null
      ws.onmessage = null
      ws.onclose = null
      ws.onerror = null
      ws.close()
      ws = null
    }
    if (source) {
      dropSource(source)
      source = null
    }
  }

  // The heartbeat listener stays attached but is inert: it checks
  // `source === events`, and a closed EventSource dispatches nothing.
  function dropSource(events: EventSource) {
    watchdog.stop()
    events.onopen = null
    events.onmessage = null
    events.onerror = null
    events.close()
  }

  // `error` and a watchdog-detected stall share this exit.
  function failSource(events: EventSource) {
    dropSource(events)
    if (source === events) source = null
    scheduleReconnect()
  }

  const scheduleReconnect = () => {
    if (closed) return
    clearTimer()
    timer = setTimeout(() => {
      timer = null
      connect()
    }, delay)
    delay = Math.min(delay * 2, maxDelay)
  }

  const handleOpen = () => {
    delay = minDelay
    setStatus('open')
  }

  function connect() {
    if (closed) return
    setStatus('connecting')
    if (protocol === 'ws') {
      const socket = new WebSocket(url)
      ws = socket
      socket.onopen = handleOpen
      socket.onmessage = (event) => {
        onEvent(null, event.data as string)
      }
      socket.onerror = () => {
        // An error does not guarantee a close event on every browser;
        // force close so the onclose handler drives the reconnect flow.
        socket.close()
      }
      socket.onclose = () => {
        if (ws === socket) ws = null
        scheduleReconnect()
      }
    } else {
      const events = new EventSource(url)
      source = events
      events.onopen = () => {
        handleOpen()
        watchdog.start(() => failSource(events))
      }
      const dispatch = (event: MessageEvent) => {
        watchdog.activity()
        onEvent(event.type === 'message' ? null : event.type, event.data)
      }
      events.onmessage = dispatch
      events.addEventListener(SSE_HEARTBEAT_EVENT, (event) => {
        if (source === events) watchdog.heartbeat((event as MessageEvent).data)
      })
      events.onerror = () => failSource(events)
    }
  }

  connect()

  return {
    close: () => {
      if (closed) return
      closed = true
      clearTimer()
      detachInstance()
      setStatus('closed')
    },
    status: () => currentStatus,
  }
}
