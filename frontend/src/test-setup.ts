import { afterEach, beforeEach, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { EventSourceMock } from './testing/eventSourceMock'
import './test-setup-matchmedia'
import './test-setup-console'

export { expectConsoleError, expectConsoleWarning } from './test-setup-console'

const preventNavigation = (event: MouseEvent) => {
  if (
    event.target instanceof Element &&
    event.target.closest('a[href]') !== null
  ) {
    event.preventDefault()
  }
}

beforeEach(() => {
  // React Router handles the click on its root before it bubbles here. This
  // preserves application navigation while suppressing jsdom's default page
  // navigation, which it intentionally does not implement.
  document.addEventListener('click', preventNavigation)
})

afterEach(() => {
  document.removeEventListener('click', preventNavigation)
})

Object.defineProperties(HTMLMediaElement.prototype, {
  play: {
    configurable: true,
    writable: true,
    value: () => Promise.resolve(),
  },
  pause: {
    configurable: true,
    writable: true,
    value: () => {},
  },
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

// jsdom 没有 DOMMatrixReadOnly；@xyflow 测量动态加入的节点时只读 m22（缩放）。
class DOMMatrixReadOnlyMock {
  m22: number

  constructor(transform: string) {
    const scale = transform.match(/scale\(([\d.]+)\)/)?.[1]
    this.m22 = scale === undefined ? 1 : Number(scale)
  }
}

Object.assign(globalThis, { DOMMatrixReadOnly: DOMMatrixReadOnlyMock })

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
