/**
 * AgentPanelDock 的缩放行为测试（姊妹文件——AgentPanelDock.test.tsx 超
 * 800 行纪律线，缩放用例零改动迁出）：顶部把手缩放到窗口顶外按 topInset
 * 钳 y 且底边不变（高度联动）；小视口下有效 minWidth 跟随视口。
 * jsdom 视口固定 1024×768，无 AppBar 元素 → topInset 走 --app-bar-height
 * 回退（56）：默认几何 x=1024-520-16=488、y=64、宽 520、高 620。
 * localStorage stub / renderDock / rndWrapper 与主测试文件同构（同
 * escape/focus 姊妹文件模式）。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { AgentPanelDock } from './AgentPanelDock'
import { loadDockPlacement } from './dockPlacementStorage'

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

beforeEach(() => {
  localStorageStub.clear()
})

describe('AgentPanelDock 缩放', () => {
  it('codex P2 复审轮：顶部把手缩放到窗口顶外时按 topInset 钳 y，且底边不变（高度联动）', async () => {
    // re-resizable 的把手用 ref.offsetWidth/Height 报新尺寸；jsdom 布局恒 0，
    // 这里把 offset* 桥到内联 style（缩放中 re-resizable 自己管理 style 尺寸）。
    const descW = Object.getOwnPropertyDescriptor(
      HTMLElement.prototype,
      'offsetWidth'
    )
    const descH = Object.getOwnPropertyDescriptor(
      HTMLElement.prototype,
      'offsetHeight'
    )
    Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
      configurable: true,
      get(this: HTMLElement) {
        const v = Number.parseFloat(this.style.width)
        return Number.isFinite(v) ? v : 0
      },
    })
    Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
      configurable: true,
      get(this: HTMLElement) {
        const v = Number.parseFloat(this.style.height)
        return Number.isFinite(v) ? v : 0
      },
    })
    try {
      renderDock()
      const surface = await screen.findByRole('dialog', { name: '测试面板' })
      const wrapper = rndWrapper(surface)
      // 顶部把手：row-resize + top:-5px（re-resizable 无默认类名，按内联
      // style 识别）。
      const topHandle = Array.from(
        wrapper.querySelectorAll('div[style*="row-resize"]')
      ).find((el) => (el as HTMLElement).style.top === '-5px')
      if (!topHandle) throw new Error('顶部缩放把手未找到')

      // 起点（488,64，高 620，底边 684）；向上大幅拖出窗口顶。
      fireEvent.mouseDown(topHandle, { clientX: 700, clientY: 64 })
      fireEvent.mouseMove(document, { clientX: 700, clientY: -500 })
      fireEvent.mouseUp(document, { clientX: 700, clientY: -500 })

      await waitFor(() => {
        const stored = loadDockPlacement('test-surface')
        // 钳制生效：y 被钳到 56（topInset 兜底）。
        expect(stored).toMatchObject({ y: 56 })
      })
      // 高度联动：height 减去钳位量，底边相对「报告的原始几何」不变。
      // jsdom 里 re-resizable 报告的坐标受 transform 自校准产物影响归零
      // （见主测试文件 jsdomTransform 注释）：报告 (y=0, height=620) → 钳后
      // (56, 564)，底边 0+620 = 56+564 = 620 不变。真实浏览器里报告值
      // 是 (-500, 1184) → 钳后 (56, 628)，底边 684 不变——这层数学由
      // dockPlacement.test.ts 的 clampResizeTopInset 纯函数测试钉住。
      const stored = loadDockPlacement('test-surface')
      expect(stored!.y + stored!.height).toBe(620)
    } finally {
      if (descW) {
        Object.defineProperty(HTMLElement.prototype, 'offsetWidth', descW)
      }
      if (descH) {
        Object.defineProperty(HTMLElement.prototype, 'offsetHeight', descH)
      }
    }
  })

  it('codex P2 复审轮：小视口（320px）下有效 minWidth 跟随视口，缩放下限与几何钳制同约束', async () => {
    const originalWidth = window.innerWidth
    const originalHeight = window.innerHeight
    Object.defineProperty(window, 'innerWidth', {
      writable: true,
      configurable: true,
      value: 320,
    })
    Object.defineProperty(window, 'innerHeight', {
      writable: true,
      configurable: true,
      value: 480,
    })
    // re-resizable 经 ref.offsetWidth/Height 报新尺寸：jsdom 布局恒 0，
    // 桥到内联 style（同顶部把手用例）。
    const descW = Object.getOwnPropertyDescriptor(
      HTMLElement.prototype,
      'offsetWidth'
    )
    const descH = Object.getOwnPropertyDescriptor(
      HTMLElement.prototype,
      'offsetHeight'
    )
    Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
      configurable: true,
      get(this: HTMLElement) {
        const v = Number.parseFloat(this.style.width)
        return Number.isFinite(v) ? v : 0
      },
    })
    Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
      configurable: true,
      get(this: HTMLElement) {
        const v = Number.parseFloat(this.style.height)
        return Number.isFinite(v) ? v : 0
      },
    })
    try {
      renderDock()
      const surface = await screen.findByRole('dialog', { name: '测试面板' })
      const wrapper = rndWrapper(surface)
      // 默认几何宽被钳到 288（320-32）；Rnd 尺寸跟随几何。
      expect(wrapper.style.width).toBe('288px')

      // 右侧把手再向左缩 100px：有效 minWidth=288（与几何钳制同约束）——
      // 宽度停在 288；若 Rnd 仍按声明 minWidth=320/340，宽度会被撑出视口。
      const rightHandle = Array.from(
        wrapper.querySelectorAll('div[style*="col-resize"]')
      ).find((el) => (el as HTMLElement).style.right === '-5px')
      if (!rightHandle) throw new Error('右侧缩放把手未找到')
      fireEvent.mouseDown(rightHandle, { clientX: 304, clientY: 300 })
      fireEvent.mouseMove(document, { clientX: 204, clientY: 300 })
      fireEvent.mouseUp(document, { clientX: 204, clientY: 300 })

      await waitFor(() =>
        expect(loadDockPlacement('test-surface')?.width).toBe(288)
      )
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
      if (descW)
        Object.defineProperty(HTMLElement.prototype, 'offsetWidth', descW)
      if (descH)
        Object.defineProperty(HTMLElement.prototype, 'offsetHeight', descH)
    }
  })
})
