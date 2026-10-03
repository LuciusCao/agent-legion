/**
 * AgentPanelDock 的拖拽行为测试（姊妹文件——AgentPanelDock.test.tsx 超
 * 800 行纪律线，拖拽用例零改动迁出）：标题栏拖拽移动并按 surfaceKey 记忆
 * 位置、重开恢复；拖拽实时钳制 y 不低于 AppBar 顶边；拖拽后 Esc 关闭写回
 * 新几何。
 * jsdom 视口固定 1024×768，无 AppBar 元素 → topInset 走 --app-bar-height
 * 回退（56）：默认几何 x=1024-520-16=488、y=64、宽 520、高 620。
 * localStorage stub / renderDock / rndWrapper / jsdomTransform 与主测试文
 * 件同构（同 escape/focus 姊妹文件模式）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
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

describe('AgentPanelDock 拖拽', () => {
  it('拖拽标题栏移动面板并按 surfaceKey 记忆位置，重开恢复到记忆位置', async () => {
    const first = renderDock()
    const surface = await screen.findByRole('dialog', { name: '测试面板' })
    const handle = screen.getByTestId('dock-test-surface-handle')

    // 起点（默认几何）：x=488、y=64；拖拽 delta (-200, +100)。
    fireEvent.mouseDown(handle, { clientX: 600, clientY: 80 })
    fireEvent.mouseMove(document, { clientX: 400, clientY: 180 })
    fireEvent.mouseUp(document, { clientX: 400, clientY: 180 })

    await waitFor(() => {
      const stored = loadDockPlacement('test-surface')
      expect(stored).toMatchObject({ x: 288, y: 164 })
    })
    // 挂载时位置 (488,64) 被冻结为 jsdom 偏移，新位置读作 (288+488, 164+64)。
    expect(rndWrapper(surface).style.transform).toBe(
      jsdomTransform(288, 164, { x: 488, y: 64 })
    )
    first.unmount()

    // 重开：同一 surfaceKey 恢复记忆位置（新挂载以记忆位置为基准）。
    const second = renderDock()
    const reopened = await screen.findByRole('dialog', { name: '测试面板' })
    expect(rndWrapper(reopened).style.transform).toBe(jsdomTransform(288, 164))
    second.unmount()
  })

  it('codex P2：拖拽实时钳制 y 不低于 AppBar 顶边（bounds=window 允许 y=0）', async () => {
    renderDock()
    const surface = await screen.findByRole('dialog', { name: '测试面板' })
    const handle = screen.getByTestId('dock-test-surface-handle')

    // 起点 y=64；向上大幅拖拽（目标 y 远低于 topInset=56 兜底）。
    fireEvent.mouseDown(handle, { clientX: 600, clientY: 80 })
    fireEvent.mouseMove(document, { clientX: 600, clientY: -500 })
    fireEvent.mouseUp(document, { clientX: 600, clientY: -500 })

    await waitFor(() => {
      const stored = loadDockPlacement('test-surface')
      // 提交钳制：y 被钳到 56（x 未动）。
      expect(stored).toMatchObject({ x: 488, y: 56 })
    })
    expect(rndWrapper(surface).style.transform).toBe(
      jsdomTransform(488, 56, { x: 488, y: 64 })
    )
  })

  it('codex P2 复审轮：拖拽改几何后按 Esc 关闭，写回存储的是新几何（Esc 回调不冻结首帧闭包）', async () => {
    const onClose = vi.fn()
    renderDock({ onClose })
    await screen.findByRole('dialog', { name: '测试面板' })
    const handle = screen.getByTestId('dock-test-surface-handle')

    // 拖拽到新位置（488,64 → 288,164）并提交存储。
    fireEvent.mouseDown(handle, { clientX: 600, clientY: 80 })
    fireEvent.mouseMove(document, { clientX: 400, clientY: 180 })
    fireEvent.mouseUp(document, { clientX: 400, clientY: 180 })
    await waitFor(() =>
      expect(loadDockPlacement('test-surface')).toMatchObject({
        x: 288,
        y: 164,
      })
    )

    // Esc 关闭（document 级）：关闭路径不写存储，几何保持拖拽提交值。
    fireEvent.keyDown(document.body, { key: 'Escape' })
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
    expect(loadDockPlacement('test-surface')).toMatchObject({
      x: 288,
      y: 164,
    })
  })
})
