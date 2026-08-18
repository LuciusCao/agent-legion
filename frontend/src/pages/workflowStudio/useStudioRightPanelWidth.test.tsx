import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import {
  clampRightPanelWidth,
  RIGHT_PANEL_MAX_WIDTH,
  RIGHT_PANEL_MIN_WIDTH,
  useStudioRightPanelWidth,
} from './useStudioRightPanelWidth'

const STORAGE_KEY = 'studio.rightPanel.width'
const CSS_VAR = '--studio-right-width'

// 该 jsdom 环境不提供 localStorage：用内存 stub 验证持久化读写。
function installLocalStorageStub() {
  const store = new Map<string, string>()
  const stub: Storage = {
    get length() {
      return store.size
    },
    clear: () => store.clear(),
    getItem: (key) => store.get(key) ?? null,
    key: (index) => [...store.keys()][index] ?? null,
    removeItem: (key) => void store.delete(key),
    setItem: (key, value) => void store.set(key, String(value)),
  }
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: stub,
  })
  return stub
}

function pointerEvent(type: string, clientX: number) {
  return new MouseEvent(type, { clientX }) as globalThis.PointerEvent
}

/** 构造一个 currentTarget 挂在指定宽度容器里的拖拽起始事件。 */
function dragStart(clientX: number, containerWidth = 0) {
  const parent = document.createElement('div')
  parent.getBoundingClientRect = () => ({ width: containerWidth }) as DOMRect
  const handle = document.createElement('div')
  parent.appendChild(handle)
  return {
    preventDefault: () => {},
    clientX,
    currentTarget: handle,
  } as unknown as React.PointerEvent
}

describe('clampRightPanelWidth', () => {
  it('clamps to min/max and rounds', () => {
    expect(clampRightPanelWidth(100)).toBe(RIGHT_PANEL_MIN_WIDTH)
    expect(clampRightPanelWidth(5000)).toBe(RIGHT_PANEL_MAX_WIDTH)
    expect(clampRightPanelWidth(480.6)).toBe(481)
  })
})

describe('useStudioRightPanelWidth', () => {
  let storage: Storage
  beforeEach(() => {
    storage = installLocalStorageStub()
    document.documentElement.style.removeProperty(CSS_VAR)
  })

  it('defaults to unset (1:1): no CSS variable, no storage write', () => {
    const { result } = renderHook(() => useStudioRightPanelWidth())
    expect(result.current.width).toBeNull()
    expect(document.documentElement.style.getPropertyValue(CSS_VAR)).toBe('')
    expect(storage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('restores and clamps the persisted width', () => {
    storage.setItem(STORAGE_KEY, '5000')
    const { result } = renderHook(() => useStudioRightPanelWidth())
    expect(result.current.width).toBe(RIGHT_PANEL_MAX_WIDTH)
  })

  it('drags from the measured half of the container when unset', () => {
    const { result } = renderHook(() => useStudioRightPanelWidth())
    act(() => {
      // 容器 1006px：右半 = (1006 - 6) / 2 = 500
      result.current.startDrag(dragStart(500, 1006))
    })
    act(() => {
      document.dispatchEvent(pointerEvent('pointermove', 400))
    })
    expect(result.current.width).toBe(600)
    expect(storage.getItem(STORAGE_KEY)).toBe('600')
    expect(document.documentElement.style.getPropertyValue(CSS_VAR)).toBe(
      '600px'
    )
  })

  it('widens when dragging left and persists to localStorage', () => {
    storage.setItem(STORAGE_KEY, '360')
    const { result } = renderHook(() => useStudioRightPanelWidth())
    act(() => {
      result.current.startDrag(dragStart(500))
    })
    act(() => {
      document.dispatchEvent(pointerEvent('pointermove', 360))
    })
    expect(result.current.width).toBe(500)
    expect(storage.getItem(STORAGE_KEY)).toBe('500')

    act(() => {
      document.dispatchEvent(pointerEvent('pointerup', 360))
    })
    // 拖拽结束后继续移动不再改宽度
    act(() => {
      document.dispatchEvent(pointerEvent('pointermove', 100))
    })
    expect(result.current.width).toBe(500)
  })

  it('never drags below the minimum width', () => {
    const { result } = renderHook(() => useStudioRightPanelWidth())
    act(() => {
      result.current.startDrag(dragStart(0))
    })
    act(() => {
      document.dispatchEvent(pointerEvent('pointermove', 5000))
    })
    expect(result.current.width).toBe(RIGHT_PANEL_MIN_WIDTH)
    act(() => {
      document.dispatchEvent(pointerEvent('pointerup', 5000))
    })
  })

  it('resets to the unset 1:1 state', () => {
    storage.setItem(STORAGE_KEY, '640')
    const { result } = renderHook(() => useStudioRightPanelWidth())
    expect(result.current.width).toBe(640)
    act(() => result.current.resetWidth())
    expect(result.current.width).toBeNull()
    expect(storage.getItem(STORAGE_KEY)).toBeNull()
    expect(document.documentElement.style.getPropertyValue(CSS_VAR)).toBe('')
  })
})
