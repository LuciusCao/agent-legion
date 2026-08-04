import { vi } from 'vitest'

export class WebSocketMock {
  static instances: WebSocketMock[] = []

  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()

  constructor(public url: string) {
    WebSocketMock.instances.push(this)
  }

  emitMessage(data: string): void {
    if (this.onmessage) {
      this.onmessage(new MessageEvent('message', { data }))
    }
  }

  static reset(): void {
    WebSocketMock.instances = []
  }
}
