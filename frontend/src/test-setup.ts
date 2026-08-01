import { afterEach, beforeEach, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { EventSourceMock } from './testing/eventSourceMock'
import './test-setup-matchmedia'

let unexpectedConsoleErrors: unknown[][] = []
let expectedConsoleErrors: RegExp[] = []
let unexpectedConsoleWarnings: unknown[][] = []
let expectedConsoleWarnings: RegExp[] = []

export function expectConsoleError(pattern: RegExp) {
  expectedConsoleErrors.push(pattern)
}

export function expectConsoleWarning(pattern: RegExp) {
  expectedConsoleWarnings.push(pattern)
}

beforeEach(() => {
  unexpectedConsoleErrors = []
  expectedConsoleErrors = []
  unexpectedConsoleWarnings = []
  expectedConsoleWarnings = []
  vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => {
    unexpectedConsoleErrors.push(args)
  })
  vi.spyOn(console, 'warn').mockImplementation((...args: unknown[]) => {
    unexpectedConsoleWarnings.push(args)
  })
})

afterEach(() => {
  vi.restoreAllMocks()
  const unmatchedErrors = unexpectedConsoleErrors
    .map((args) => args.map(String).join(' '))
    .filter(
      (message) =>
        !expectedConsoleErrors.some((pattern) => pattern.test(message))
    )
  const unmatchedWarnings = unexpectedConsoleWarnings
    .map((args) => args.map(String).join(' '))
    .filter(
      (message) =>
        !expectedConsoleWarnings.some((pattern) => pattern.test(message))
    )
  const unexpectedMessages = [
    ...unmatchedErrors.map((message) => `console.error: ${message}`),
    ...unmatchedWarnings.map((message) => `console.warn: ${message}`),
  ]
  if (unexpectedMessages.length === 0) return
  throw new Error(
    `Unexpected console output during test:\n${unexpectedMessages.join('\n')}`
  )
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
