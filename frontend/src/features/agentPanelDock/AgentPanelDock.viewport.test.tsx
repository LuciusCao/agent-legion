/**
 * AgentPanelDock 的视口钳制测试（姊妹文件——AgentPanelDock.test.tsx 超
 * 800 行纪律线，视口钳制用例零改动迁出）：AppBar 实测高度到达/自身变高
 * （ResizeObserver）后重钳记忆几何；视口缩小重钳；topInsetExtra（窄屏页
 * 签导航高度）叠加进默认几何与拖拽钳制；矮视口下高度封顶于可用空间。
 * jsdom 视口固定 1024×768，无 AppBar 元素 → topInset 走 --app-bar-height
 * 回退（56）：默认几何 x=1024-520-16=488、y=64、宽 520、高 620。
 * localStorage stub / renderDock / rndWrapper / jsdomTransform 与主测试文
 * 件同构（同 escape/focus 姊妹文件模式）。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { AgentPanelDock } from './AgentPanelDock'
import { dockStorageKey, loadDockPlacement } from './dockPlacementStorage'

// 该 jsdom 环境不提供 localStorage：用内存 stub 验证持久化读写（同主测试
// 文件模式）。
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

const localStorageStub = installLocalStorageStub()

function renderDock(
  props?: Partial<Parameters<typeof AgentPanelDock>[0]>,
  child?: ReactElement
) {
  return render(
    (
      <AgentPanelDock
        surfaceKey="test-surface"
        title="测试面板"
        onClose={() => undefined}
        {...props}
      >
        {child ?? <div data-testid="dock-child">面板内容</div>}
      </AgentPanelDock>
    ) as ReactElement
  )
}

/** Rnd 外层 wrapper（承载 fixed 定位 / transform / z-index 的内联 style）。 */
function rndWrapper(surface: HTMLElement): HTMLElement {
  const wrapper = surface.parentElement
  if (!wrapper) throw new Error('Rnd wrapper 不存在')
  return wrapper
}

/**
 * jsdom 下的 transform 期望值。react-rnd 挂载时经 getBoundingClientRect
 * 自校准父级偏移（offsetFromParent = selfRect − parentRect − position）：
 * 真实浏览器里元素已被 transform 移到位，校准收敛为恒等（transform =
 * position）；jsdom 矩形恒为 0，校准把「−挂载时位置」冻结为偏移，于是
 * 挂载后 transform 读作 position + 挂载时位置（首次挂载即 2 倍）。这是
 * 测试环境产物而非组件缺陷——记忆/恢复的权威断言落在 localStorage。
 */
function jsdomTransform(x: number, y: number, mountedAt = { x, y }): string {
  // jsdom 序列化内联 transform 时去掉逗号后的空格。
  return `translate(${x + mountedAt.x}px,${y + mountedAt.y}px)`
}

beforeEach(() => {
  localStorageStub.clear()
})

describe('AgentPanelDock 视口钳制', () => {
  it('codex P2：AppBar 实测高度（>56 兜底）到达后重新钳制记忆位置', async () => {
    // 记忆位置 y=70：兜底 56 下合法（≥56），实测 AppBar 底边 100 下越界。
    window.localStorage.setItem(
      dockStorageKey('test-surface'),
      JSON.stringify({
        x: 900,
        y: 70,
        width: 520,
        height: 620,
      })
    )
    // 假 AppBar：实测底边 100（版本芯片/放大字体场景）。
    const fakeBar = document.createElement('div')
    fakeBar.setAttribute('data-testid', 'app-bar')
    fakeBar.getBoundingClientRect = () => ({ bottom: 100 }) as DOMRect
    document.body.appendChild(fakeBar)
    try {
      renderDock()
      const surface = await screen.findByRole('dialog', { name: '测试面板' })
      // 实测到达后重钳：y 70 → 100。transform 读数带挂载时位置的 jsdom
      // 偏移（挂载时 y=70）。
      await waitFor(() =>
        expect(rndWrapper(surface).style.transform).toBe(
          jsdomTransform(900, 100, { x: 900, y: 70 })
        )
      )
    } finally {
      fakeBar.remove()
    }
  })

  it('codex P2：视口缩小时重新钳制记忆几何（尺寸+坐标收进新视口）', async () => {
    // 记忆几何宽 1200：1024 视口下钳到 992。
    window.localStorage.setItem(
      dockStorageKey('test-surface'),
      JSON.stringify({
        x: 24,
        y: 100,
        width: 1200,
        height: 620,
      })
    )
    renderDock()
    const surface = await screen.findByRole('dialog', { name: '测试面板' })
    expect(rndWrapper(surface).style.width).toBe('992px')

    // 视口缩到 640×480：宽度钳到 608（把手/按钮收进视口）。
    const originalWidth = window.innerWidth
    const originalHeight = window.innerHeight
    Object.defineProperty(window, 'innerWidth', {
      writable: true,
      configurable: true,
      value: 640,
    })
    Object.defineProperty(window, 'innerHeight', {
      writable: true,
      configurable: true,
      value: 480,
    })
    try {
      fireEvent(window, new Event('resize'))
      await waitFor(() => expect(rndWrapper(surface).style.width).toBe('608px'))
    } finally {
      Object.defineProperty(window, 'innerWidth', {
        writable: true,
        configurable: true,
        value: originalWidth,
      })
      Object.defineProperty(window, 'innerHeight', {
        writable: true,
        configurable: true,
        value: originalHeight,
      })
    }
  })

  it('codex P2 复审轮 6：topInsetExtra（窄屏页签导航高度）叠加进默认几何与拖拽钳制', async () => {
    // 窄屏 Dock 几乎占满视口宽：AppBar 下方的移动端页签导航也要避让——
    // 顶边 = AppBar 底边（兜底 56）+ nav 实测高（40）+ 8。
    renderDock({ topInsetExtra: 40 })
    const surface = await screen.findByRole('dialog', { name: '测试面板' })
    expect(rndWrapper(surface).style.transform).toBe(jsdomTransform(488, 104))

    // 拖拽到 y=0：钳到 96（56+40），不是 56——只改默认几何的话拖拽还能
    // 拖上去盖住页签（revert topInsetExtra 进钳制即红）。
    const handle = screen.getByTestId('dock-test-surface-handle')
    fireEvent.mouseDown(handle, { clientX: 600, clientY: 120 })
    fireEvent.mouseMove(document, { clientX: 600, clientY: -500 })
    fireEvent.mouseUp(document, { clientX: 600, clientY: -500 })
    await waitFor(() => {
      const stored = loadDockPlacement('test-surface')
      expect(stored).toMatchObject({ y: 96 })
    })
  })

  it('codex P2 复审轮 7：320px 高视口 + topInsetExtra 下默认几何高度封顶于可用空间（底部不出视口）', async () => {
    const originalWidth = window.innerWidth
    const originalHeight = window.innerHeight
    // 横屏手机：640×320，AppBar 56 + 页签 48 → topInset=104，
    // 可用高 = 320-104-32=184。旧实现 Math.max(240,…) 强制 240 → 底部
    // 112+240=352 出视口（composer/发送按钮不可见）。
    Object.defineProperty(window, 'innerWidth', {
      writable: true,
      configurable: true,
      value: 640,
    })
    Object.defineProperty(window, 'innerHeight', {
      writable: true,
      configurable: true,
      value: 320,
    })
    try {
      renderDock({ topInsetExtra: 48 })
      const surface = await screen.findByRole('dialog', { name: '测试面板' })
      const wrapper = rndWrapper(surface)
      // 高度封顶 184；y=112（transform 读数 2 倍挂载位置）→ 底边 296 ≤ 320。
      expect(wrapper.style.height).toBe('184px')
      expect(wrapper.style.transform).toBe('translate(208px,224px)')
    } finally {
      Object.defineProperty(window, 'innerWidth', {
        writable: true,
        configurable: true,
        value: originalWidth,
      })
      Object.defineProperty(window, 'innerHeight', {
        writable: true,
        configurable: true,
        value: originalHeight,
      })
    }
  })

  it('codex P2 复审轮：AppBar 自身变高（ResizeObserver 监听元素）后 Dock 几何重钳', async () => {
    // test-setup 的 ResizeObserverMock 在 observe 时立即回调一次；这里包装
    // 它以捕获回调，模拟「AppBar 异步变高」（版本芯片/字体加载把 AppBar
    // 自己撑高，window.resize 不触发）。
    const NativeResizeObserver = globalThis.ResizeObserver
    let captured: ResizeObserverCallback | null = null
    class CapturingObserver extends NativeResizeObserver {
      constructor(callback: ResizeObserverCallback) {
        super(callback)
        captured = callback
      }
    }
    globalThis.ResizeObserver = CapturingObserver

    let barBottom = 100
    const fakeBar = document.createElement('div')
    fakeBar.setAttribute('data-testid', 'app-bar')
    fakeBar.getBoundingClientRect = () => ({ bottom: barBottom }) as DOMRect
    document.body.appendChild(fakeBar)
    // 记忆位置 y=70：实测 100 下越界 → 先钳到 100。
    window.localStorage.setItem(
      dockStorageKey('test-surface'),
      JSON.stringify({
        x: 900,
        y: 70,
        width: 520,
        height: 620,
      })
    )
    try {
      renderDock()
      const surface = await screen.findByRole('dialog', { name: '测试面板' })
      await waitFor(() =>
        expect(rndWrapper(surface).style.transform).toBe(
          jsdomTransform(900, 100, { x: 900, y: 70 })
        )
      )

      // AppBar 变高 100 → 140（window 尺寸未变，只有元素自身变了）。
      barBottom = 140
      act(() => {
        captured?.([], {} as ResizeObserver)
      })
      await waitFor(() =>
        expect(rndWrapper(surface).style.transform).toBe(
          jsdomTransform(900, 140, { x: 900, y: 70 })
        )
      )
    } finally {
      fakeBar.remove()
      globalThis.ResizeObserver = NativeResizeObserver
    }
  })
})
