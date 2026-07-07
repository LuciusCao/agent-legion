import { vi } from 'vitest'

export class EventSourceMock {
  static instances: EventSourceMock[] = []

  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()

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

  static reset(): void {
    EventSourceMock.instances = []
  }
}
