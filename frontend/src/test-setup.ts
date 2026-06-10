import { vi } from 'vitest'
import '@testing-library/jest-dom/vitest'

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
  callback: any
  entries: any[] = []

  constructor(callback: any) {
    this.callback = callback
  }

  observe = vi.fn((target: any) => {
    this.entries.push({
      target,
      isIntersecting: true,
      intersectionRatio: 1,
      boundingClientRect: {},
      intersectionRect: {},
      rootBounds: null,
      time: Date.now(),
    })
    this.callback(this.entries, this)
  })

  disconnect = vi.fn()
  unobserve = vi.fn()
}

;(globalThis as any).IntersectionObserver = IntersectionObserverMock
