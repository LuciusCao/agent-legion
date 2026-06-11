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

  static reset(): void {
    EventSourceMock.instances = []
  }
}
