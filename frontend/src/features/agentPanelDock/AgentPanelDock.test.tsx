/**
 * AgentPanelDock 容器行为测试（issue #795 PR①）：
 * - 非模态 surface：role=dialog + aria-modal=false、z-index 900、无 MUI
 *   Modal/遮罩、底层页面不进 aria-hidden；
 * - 记忆：位置/尺寸按 surfaceKey 存 localStorage，重开恢复；存量
 *   collapsed 字段读取时忽略（#795 收尾：折叠态移除，开/关两态）；
 * - Esc 关闭（走 onClose）；hidden 隐藏不卸载、子树 state 存活。
 * #809：文件超 800 行纪律线，拖拽/缩放/视口钳制/rightInset 避让用例零改
 * 动迁出至同目录姊妹文件（AgentPanelDock.drag/resize/viewport/rightInset
 * .test.tsx）。
 * jsdom 视口固定 1024×768，无 AppBar 元素 → topInset 走 --app-bar-height
 * 回退（56）：默认几何 x=1024-520-16=488、y=64、宽 520、高 620。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { Dialog } from '@mui/material'
import { AgentPanelDock } from './AgentPanelDock'
import { dockStorageKey, loadDockPlacement } from './dockPlacementStorage'

// 该 jsdom 环境不提供 localStorage：用内存 stub 验证持久化读写（同
// useStudioChat.test.tsx / StudioChatResume.test.tsx 的模式）。
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

describe('AgentPanelDock', () => {
  it('非模态 surface：role=dialog + aria-modal=false、fixed 定位 z-index 900、无遮罩不锁滚动', async () => {
    render(
      (
        <div>
          <button type="button">底层按钮</button>
          <AgentPanelDock
            surfaceKey="test-surface"
            title="测试面板"
            onClose={() => undefined}
          >
            <div>内容</div>
          </AgentPanelDock>
        </div>
      ) as ReactElement
    )
    const surface = await screen.findByRole('dialog', { name: '测试面板' })
    expect(surface).toHaveAttribute('aria-modal', 'false')
    const wrapper = rndWrapper(surface)
    expect(wrapper.style.position).toBe('fixed')
    expect(wrapper.style.zIndex).toBe('900')
    expect(document.querySelector('.MuiModal-root')).toBeNull()
    expect(document.querySelector('.MuiBackdrop-root')).toBeNull()
    expect(document.body.style.overflow).not.toBe('hidden')
    const underlying = screen.getByRole('button', { name: '底层按钮' })
    expect(underlying.closest('[aria-hidden="true"]')).toBeNull()
    // 默认位置：贴右缘、顶边让开 AppBar 声明高度（56+8）——存储坐标
    // (488,64)；transform 读数带 jsdom 偏移产物（见 jsdomTransform）。
    expect(wrapper.style.transform).toBe(jsdomTransform(488, 64))
  })

  it('标题栏只有关闭按钮，不渲染折叠 chip（#795 收尾：折叠态移除）', async () => {
    renderDock()
    await screen.findByRole('dialog', { name: '测试面板' })
    expect(screen.getByRole('button', { name: '关闭' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '折叠面板' })).toBeNull()
    expect(screen.queryByRole('button', { name: /已折叠/ })).toBeNull()
  })

  it('hidden 隐藏不卸载（#797 codex P1）：surface 不渲染，子树 state 存活，恢复后原样', async () => {
    const { rerender } = renderDock(
      {},
      <input data-testid="dock-child" defaultValue="" />
    )
    const surface = await screen.findByRole('dialog', { name: '测试面板' })
    fireEvent.change(screen.getByTestId('dock-child'), {
      target: { value: '未发送草稿' },
    })

    // hidden=true：display:none 抑制（同一条 Rnd>Paper>内容树，
    // 不换元素类型不重挂）。
    rerender(
      (
        <AgentPanelDock
          surfaceKey="test-surface"
          title="测试面板"
          onClose={() => undefined}
          hidden
        >
          <input data-testid="dock-child" defaultValue="" />
        </AgentPanelDock>
      ) as ReactElement
    )
    expect(rndWrapper(surface).style.display).toBe('none')
    // 子树保持挂载且 state 存活（卸载即丢——revert 即红）。
    expect(screen.getByTestId('dock-child')).toHaveValue('未发送草稿')

    rerender(
      (
        <AgentPanelDock
          surfaceKey="test-surface"
          title="测试面板"
          onClose={() => undefined}
          hidden={false}
        >
          <input data-testid="dock-child" defaultValue="" />
        </AgentPanelDock>
      ) as ReactElement
    )
    expect(rndWrapper(surface).style.display).not.toBe('none')
    expect(screen.getByTestId('dock-child')).toHaveValue('未发送草稿')
  })

  it('Esc 关闭面板（#795 收尾：语义从折叠改为关闭，走 onClose 同标题栏关闭）', async () => {
    const onClose = vi.fn()
    renderDock({ onClose })
    const surface = await screen.findByRole('dialog', { name: '测试面板' })
    fireEvent.keyDown(surface, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('关闭按钮调用 onClose', async () => {
    const onClose = vi.fn()
    renderDock({ onClose })
    await screen.findByRole('dialog', { name: '测试面板' })
    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('存量 collapsed=true 记忆被忽略（#795 收尾）：按几何恢复、不渲染小条', async () => {
    // 折叠态移除前写入的旧数据：collapsed 字段读取时直接忽略——面板正常
    // 展开呈现，不复活 chip、不崩溃。
    localStorageStub.setItem(
      'agent-panel-dock:test-surface',
      JSON.stringify({
        x: 100,
        y: 100,
        width: 480,
        height: 400,
        collapsed: true,
      })
    )
    renderDock()
    const surface = await screen.findByRole('dialog', { name: '测试面板' })
    expect(screen.queryByRole('button', { name: /已折叠/ })).toBeNull()
    expect(rndWrapper(surface).style.display).not.toBe('none')
    // 读取侧只认几何字段。
    expect(loadDockPlacement('test-surface')).toEqual({
      x: 100,
      y: 100,
      width: 480,
      height: 400,
    })
  })

  it('不同 surfaceKey 的记忆互相隔离', async () => {
    const first = renderDock()
    await screen.findByRole('dialog', { name: '测试面板' })
    // 拖拽一次让记忆落盘（折叠写入已随 #795 收尾移除）。
    const handle = screen.getByTestId('dock-test-surface-handle')
    fireEvent.mouseDown(handle, { clientX: 600, clientY: 80 })
    fireEvent.mouseMove(document, { clientX: 400, clientY: 180 })
    fireEvent.mouseUp(document, { clientX: 400, clientY: 180 })
    await waitFor(() =>
      expect(loadDockPlacement('test-surface')).not.toBeNull()
    )
    first.unmount()

    // 另一个 surface：不受 test-surface 的记忆影响。
    renderDock({ surfaceKey: 'other-surface', title: '另一面板' })
    expect(
      await screen.findByRole('dialog', { name: '另一面板' })
    ).toBeInTheDocument()
    expect(loadDockPlacement('other-surface')).toBeNull()
    expect(
      window.localStorage.getItem(dockStorageKey('test-surface'))
    ).not.toBeNull()
  })

  it('损坏的 localStorage 值回退默认布局', async () => {
    window.localStorage.setItem(dockStorageKey('test-surface'), '{not json')
    renderDock()
    const surface = await screen.findByRole('dialog', { name: '测试面板' })
    expect(rndWrapper(surface).style.transform).toBe(jsdomTransform(488, 64))
  })

  it('codex P2：Esc 关闭挂在 document 级——焦点在面板外（底层页面）也生效', async () => {
    const onClose = vi.fn()
    render(
      (
        <div>
          <button type="button">底层按钮</button>
          <AgentPanelDock
            surfaceKey="test-surface"
            title="测试面板"
            onClose={onClose}
          >
            <div data-testid="dock-child">内容</div>
          </AgentPanelDock>
        </div>
      ) as ReactElement
    )
    await screen.findByRole('dialog', { name: '测试面板' })
    // 焦点移交到底层页面（模拟用户点回页面）。
    screen.getByRole('button', { name: '底层按钮' }).focus()
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('codex P2：有全局 MUI Modal 开着时 Esc 让给对方（不关闭 Dock）', async () => {
    const onClose = vi.fn()
    render(
      (
        <div>
          <AgentPanelDock
            surfaceKey="test-surface"
            title="测试面板"
            onClose={onClose}
          >
            <div>内容</div>
          </AgentPanelDock>
          <Dialog open onClose={() => undefined}>
            <div>模态内容</div>
          </Dialog>
        </div>
      ) as ReactElement
    )
    await screen.findByRole('dialog', { name: '测试面板' })
    fireEvent.keyDown(document.body, { key: 'Escape' })
    // Dock 不关闭（Esc 属于模态）。
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog', { name: '测试面板' })).toBeInTheDocument()
  })
})
