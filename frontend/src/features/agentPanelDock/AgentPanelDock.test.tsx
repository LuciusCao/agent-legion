/**
 * AgentPanelDock 容器行为测试（issue #795 PR①）：
 * - 非模态 surface：role=dialog + aria-modal=false、z-index 900、无 MUI
 *   Modal/遮罩、底层页面不进 aria-hidden；
 * - 折叠/展开：折叠为右下角小条，内容区只 display:none 不卸载（子树状态
 *   保留），点小条展开；
 * - 记忆：位置/尺寸/折叠态按 surfaceKey 存 localStorage，重开恢复；
 * - Esc 折叠（非破坏性）；焦点移交：打开/展开进面板、折叠到小条、卸载还原。
 * jsdom 视口固定 1024×768，无 AppBar 元素 → topInset 走 --app-bar-height
 * 回退（56）：默认几何 x=1024-520-16=488、y=64、宽 520、高 620。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { AgentPanelDock } from './AgentPanelDock'
import { dockStorageKey, loadDockPlacement } from './dockPlacement'
import { expectConsoleError, expectConsoleWarning } from '../../test-setup'

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

  it('折叠为右下角小条后内容子树保持挂载，点小条展开恢复', async () => {
    renderDock()
    const surface = await screen.findByRole('dialog', { name: '测试面板' })
    fireEvent.click(screen.getByRole('button', { name: '折叠面板' }))

    const chip = await screen.findByRole('button', {
      name: '测试面板（已折叠，点击展开）',
    })
    // 折叠只是 display:none 隐藏容器（卸载会丢子树本地 state）——
    // 内容仍在 DOM 里。
    expect(screen.getByTestId('dock-child')).toBeInTheDocument()
    expect(rndWrapper(surface).style.display).toBe('none')

    fireEvent.click(chip)
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: /已折叠，点击展开/ })
      ).toBeNull()
    )
    expect(rndWrapper(surface).style.display).not.toBe('none')
  })

  it('折叠/展开不丢面板内容状态（输入值原样保留）', async () => {
    renderDock({}, <input data-testid="dock-child" defaultValue="" />)
    await screen.findByRole('dialog', { name: '测试面板' })
    const input = screen.getByTestId('dock-child')
    fireEvent.change(input, { target: { value: '未发送草稿' } })

    fireEvent.click(screen.getByRole('button', { name: '折叠面板' }))
    const chip = await screen.findByRole('button', { name: /已折叠，点击展开/ })
    expect(screen.getByTestId('dock-child')).toHaveValue('未发送草稿')

    fireEvent.click(chip)
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: /已折叠，点击展开/ })
      ).toBeNull()
    )
    expect(screen.getByTestId('dock-child')).toHaveValue('未发送草稿')
  })

  it('Esc 折叠面板（非破坏性——内容保持挂载，可从小条恢复）', async () => {
    renderDock()
    const surface = await screen.findByRole('dialog', { name: '测试面板' })
    fireEvent.keyDown(surface, { key: 'Escape' })
    expect(
      await screen.findByRole('button', { name: /已折叠，点击展开/ })
    ).toBeInTheDocument()
    expect(screen.getByTestId('dock-child')).toBeInTheDocument()
  })

  it('关闭按钮调用 onClose', async () => {
    const onClose = vi.fn()
    renderDock({ onClose })
    await screen.findByRole('dialog', { name: '测试面板' })
    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('焦点移交：打开进面板、折叠到小条、展开回面板、卸载还原触发元素', async () => {
    // 焦点移交 effect 驱动 Tooltip/ButtonBase 状态更新脱离 act（known
    // noise，与 previewPanel 既有用例同款声明）。
    expectConsoleWarning(/not wrapped in act/)
    expectConsoleError(/not wrapped in act/)
    const trigger = document.createElement('button')
    document.body.appendChild(trigger)
    trigger.focus()

    const { unmount } = renderDock()
    const surface = await screen.findByRole('dialog', { name: '测试面板' })
    await waitFor(() => expect(document.activeElement).toBe(surface))

    fireEvent.click(screen.getByRole('button', { name: '折叠面板' }))
    const chip = await screen.findByRole('button', { name: /已折叠，点击展开/ })
    await waitFor(() => expect(document.activeElement).toBe(chip))

    fireEvent.click(chip)
    await waitFor(() => expect(document.activeElement).toBe(surface))

    unmount()
    expect(document.activeElement).toBe(trigger)
    trigger.remove()
  })

  it('折叠态按 surfaceKey 记忆：重开面板直接呈现小条', async () => {
    const first = renderDock()
    await screen.findByRole('dialog', { name: '测试面板' })
    fireEvent.click(screen.getByRole('button', { name: '折叠面板' }))
    await screen.findByRole('button', { name: /已折叠，点击展开/ })
    expect(loadDockPlacement('test-surface')?.collapsed).toBe(true)
    first.unmount()

    renderDock()
    // 记忆折叠态：不再出现展开的 surface，直接渲染小条。
    expect(
      await screen.findByRole('button', { name: /已折叠，点击展开/ })
    ).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: '测试面板' })).toBeNull()
    expect(screen.getByTestId('dock-child')).toBeInTheDocument()
  })

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
      expect(stored).toMatchObject({ x: 288, y: 164, collapsed: false })
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

  it('不同 surfaceKey 的记忆互相隔离', async () => {
    const first = renderDock()
    await screen.findByRole('dialog', { name: '测试面板' })
    fireEvent.click(screen.getByRole('button', { name: '折叠面板' }))
    await screen.findByRole('button', { name: /已折叠，点击展开/ })
    first.unmount()

    // 另一个 surface：不受 test-surface 的折叠记忆影响。
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
})
