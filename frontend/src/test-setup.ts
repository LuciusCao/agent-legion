import { afterEach, beforeEach, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { EventSourceMock } from './testing/eventSourceMock'

let unexpectedConsoleErrors: unknown[][] = []
let expectedConsoleErrors: RegExp[] = []

export function expectConsoleError(pattern: RegExp) {
  expectedConsoleErrors.push(pattern)
}

beforeEach(() => {
  unexpectedConsoleErrors = []
  expectedConsoleErrors = []
  vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => {
    unexpectedConsoleErrors.push(args)
  })
})

afterEach(() => {
  vi.restoreAllMocks()
  const messages = unexpectedConsoleErrors.map((args) =>
    args.map(String).join(' ')
  )
  const unmatchedMessages = messages.filter(
    (message) => !expectedConsoleErrors.some((pattern) => pattern.test(message))
  )
  if (unmatchedMessages.length === 0) return
  const details = unmatchedMessages.join('\n')
  throw new Error(`Unexpected console.error during test:\n${details}`)
})

class ResizeObserverMock {
  callback: ResizeObserverCallback

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback
  }

  observe = vi.fn((target: Element) => {
    this.callback(
      [
        {
          target,
          contentRect: {
            height: 500,
            width: 800,
            top: 0,
            left: 0,
            bottom: 500,
            right: 800,
            x: 0,
            y: 0,
          },
          borderBoxSize: [{ blockSize: 500, inlineSize: 800 }],
          contentBoxSize: [{ blockSize: 500, inlineSize: 800 }],
          devicePixelContentBoxSize: [{ blockSize: 500, inlineSize: 800 }],
        } as unknown as ResizeObserverEntry,
      ],
      this as unknown as ResizeObserver
    )
  })

  disconnect = vi.fn()
  unobserve = vi.fn()
}

;(
  globalThis as unknown as { ResizeObserver: typeof ResizeObserver }
).ResizeObserver = ResizeObserverMock as unknown as typeof ResizeObserver

class IntersectionObserverMock {
  callback: IntersectionObserverCallback
  entries: IntersectionObserverEntry[] = []

  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback
  }

  observe = vi.fn((target: Element) => {
    this.entries.push({
      target,
      isIntersecting: true,
      intersectionRatio: 1,
      boundingClientRect: {} as DOMRectReadOnly,
      intersectionRect: {} as DOMRectReadOnly,
      rootBounds: null,
      time: Date.now(),
    } as IntersectionObserverEntry)
    this.callback(this.entries, this as unknown as IntersectionObserver)
  })

  disconnect = vi.fn()
  unobserve = vi.fn()
}

;(
  globalThis as unknown as { IntersectionObserver: typeof IntersectionObserver }
).IntersectionObserver =
  IntersectionObserverMock as unknown as typeof IntersectionObserver
;(globalThis as unknown as { EventSource: typeof EventSource }).EventSource =
  EventSourceMock as unknown as typeof EventSource
