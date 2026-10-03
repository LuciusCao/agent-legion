/**
 * AgentPanelDock 的 rightInset 避让测试（姊妹文件——AgentPanelDock.test.tsx
 * 超 800 行纪律线，rightInset 用例零改动迁出）：右侧抽屉打开时 Dock 运行
 * 时左移避让、不写布局记忆、关掉弹回原位；避让期间的拖拽/缩放提交还原为
 * 基础坐标。
 * jsdom 视口固定 1024×768，无 AppBar 元素 → topInset 走 --app-bar-height
 * 回退（56）：默认几何 x=1024-520-16=488、y=64、宽 520、高 620。
 * localStorage stub / renderDock / rndWrapper / jsdomTransform 与主测试文
 * 件同构（同 escape/focus 姊妹文件模式）。
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

describe('AgentPanelDock rightInset 避让', () => {
  it('轮 9 P2：rightInset（右侧抽屉打开）时 Dock 运行时左移避让、不写布局记忆、关掉弹回原位', async () => {
    // jsdom 视口 1024：抽屉左缘 1024-728=296；默认 Dock x=488 w=520 越界
    // → 左移到 max(8, 296-520-12)=8（钳左缘）。布局记忆不写入（重开仍回
    // 原位由「无存档」钉住）。revert：摘掉 offsetForRightInset 接线即红
    // （transform 回到 488 档）。
    const { unmount } = renderDock({ rightInset: 728 })
    const surface = await screen.findByRole('dialog', { name: '测试面板' })
    expect(rndWrapper(surface).style.transform).toBe(jsdomTransform(8, 64))
    expect(loadDockPlacement('test-surface')).toBeNull()
    unmount()

    // 无 rightInset（抽屉关闭）→ 回默认原位。
    renderDock()
    const surface2 = await screen.findByRole('dialog', { name: '测试面板' })
    expect(rndWrapper(surface2).style.transform).toBe(jsdomTransform(488, 64))
  })

  it('#779 终审 P2：rightInset 避让期间拖拽，提交还原为基础坐标（关抽屉弹回原位）', async () => {
    // 视口 1600、rightInset=728：抽屉左缘 872；默认几何 x=1064 w=520 越界
    // → 运行时渲染 x=340（实际应用的偏移 -724）。向左拖 300px：回调报告的
    // 是避让后的屏幕坐标 40，提交必须还原为基础坐标 764=1064-300——否则临
    // 时避让被烙进状态与记忆，关抽屉后面板停在 40 而非 764（revert：去掉
    // 提交前的偏移还原即红，存储读作 40）。
    const originalWidth = window.innerWidth
    Object.defineProperty(window, 'innerWidth', {
      writable: true,
      configurable: true,
      value: 1600,
    })
    try {
      const { rerender } = renderDock({ rightInset: 728 })
      const surface = await screen.findByRole('dialog', { name: '测试面板' })
      // 挂载即避让：渲染 x=340（transform 读数 2 倍挂载位置，见
      // jsdomTransform 注释）。
      expect(rndWrapper(surface).style.transform).toBe(jsdomTransform(340, 64))
      const handle = screen.getByTestId('dock-test-surface-handle')

      fireEvent.mouseDown(handle, { clientX: 600, clientY: 80 })
      fireEvent.mouseMove(document, { clientX: 300, clientY: 180 })
      fireEvent.mouseUp(document, { clientX: 300, clientY: 180 })

      await waitFor(() => {
        // localStorage 存的是还原后的基础坐标，不含运行时避让。
        expect(loadDockPlacement('test-surface')).toMatchObject({
          x: 764,
          y: 164,
        })
      })
      // 抽屉仍开着：764+520=1284 仍越抽屉左缘 872 → 渲染保持避让位 340。
      expect(rndWrapper(surface).style.transform).toBe(
        jsdomTransform(340, 164, { x: 340, y: 64 })
      )

      // 关抽屉（inset=0）：回到拖拽后的基础坐标 764，不再叠加避让。
      rerender(
        (
          <AgentPanelDock
            surfaceKey="test-surface"
            title="测试面板"
            onClose={() => undefined}
            rightInset={0}
          >
            <div data-testid="dock-child">面板内容</div>
          </AgentPanelDock>
        ) as ReactElement
      )
      await waitFor(() =>
        expect(rndWrapper(surface).style.transform).toBe(
          jsdomTransform(764, 164, { x: 340, y: 64 })
        )
      )
    } finally {
      Object.defineProperty(window, 'innerWidth', {
        writable: true,
        configurable: true,
        value: originalWidth,
      })
    }
  })

  it('#779 终审 P2：rightInset 避让期间缩放，提交同样还原基础坐标', async () => {
    // 与拖拽路径同一修复：onResizeStop 提交的 position.x 也是避让后的屏
    // 幕坐标，必须减去交互起点捕获的偏移（本例 -724）再写状态/记忆。
    // jsdom 里 re-resizable 报告的坐标受 transform 自校准产物影响归零
    // （见 jsdomTransform 注释）——报告 x=0，还原后存储应读 724=0-(-724)；
    // revert（缩放路径不还原）存储读作 0，即红。真实浏览器里报告值是屏
    // 幕坐标，同一减法还原为基础坐标。
    const originalWidth = window.innerWidth
    Object.defineProperty(window, 'innerWidth', {
      writable: true,
      configurable: true,
      value: 1600,
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
      renderDock({ rightInset: 728 })
      const surface = await screen.findByRole('dialog', { name: '测试面板' })
      const wrapper = rndWrapper(surface)
      // 左侧把手：col-resize + left:-5px。
      const leftHandle = Array.from(
        wrapper.querySelectorAll('div[style*="col-resize"]')
      ).find((el) => (el as HTMLElement).style.left === '-5px')
      if (!leftHandle) throw new Error('左侧缩放把手未找到')

      fireEvent.mouseDown(leftHandle, { clientX: 340, clientY: 300 })
      fireEvent.mouseMove(document, { clientX: 240, clientY: 300 })
      fireEvent.mouseUp(document, { clientX: 240, clientY: 300 })

      await waitFor(() => {
        const stored = loadDockPlacement('test-surface')
        // 报告 x=0（jsdom 归零产物）减去起点偏移 -724 → 724；revert 即 0。
        expect(stored).toMatchObject({ x: 724 })
      })
    } finally {
      Object.defineProperty(window, 'innerWidth', {
        writable: true,
        configurable: true,
        value: originalWidth,
      })
      if (descW)
        Object.defineProperty(HTMLElement.prototype, 'offsetWidth', descW)
      if (descH)
        Object.defineProperty(HTMLElement.prototype, 'offsetHeight', descH)
    }
  })
})
