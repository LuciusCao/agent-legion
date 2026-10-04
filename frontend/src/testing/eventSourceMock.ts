import { vi } from 'vitest'

export class EventSourceMock {
  static instances: EventSourceMock[] = []

  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
  private listeners = new Map<string, Set<(event: Event) => void>>()

  constructor(public url: string) {
    EventSourceMock.instances.push(this)
  }

  emitMessage(payload: object): void {
    if (this.onmessage) {
      this.onmessage(
        new MessageEvent('message', { data: JSON.stringify(payload) })
      )
    }
  }

  addEventListener(type: string, listener: (event: Event) => void): void {
    const set = this.listeners.get(type) ?? new Set()
    set.add(listener)
    this.listeners.set(type, set)
  }

  removeEventListener(type: string, listener: (event: Event) => void): void {
    this.listeners.get(type)?.delete(listener)
  }

  /** Dispatch a named SSE event (e.g. `heartbeat`) to its listeners. */
  emitNamed(type: string, data: string): void {
    const event = new MessageEvent(type, { data })
    for (const listener of this.listeners.get(type) ?? []) listener(event)
  }

  /** #914: the server's data-carrying heartbeat event. */
  emitHeartbeat(intervalMs = 15000): void {
    this.emitNamed('heartbeat', JSON.stringify({ interval_ms: intervalMs }))
  }

  static reset(): void {
    EventSourceMock.instances = []
  }
}
