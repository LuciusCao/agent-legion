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
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'
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

  it('hidden 隐藏不卸载（#797 codex P1）：surface 与小条都不渲染，子树 state 存活，恢复后原样', async () => {
    const { rerender } = renderDock(
      {},
      <input data-testid="dock-child" defaultValue="" />
    )
    const surface = await screen.findByRole('dialog', { name: '测试面板' })
    fireEvent.change(screen.getByTestId('dock-child'), {
      target: { value: '未发送草稿' },
    })

    // hidden=true：与折叠共用 display:none 抑制（同一条 Rnd>Paper>内容树，
    // 不换元素类型不重挂），但连小条也不渲染。
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
    expect(
      screen.queryByRole('button', { name: /已折叠，点击展开/ })
    ).toBeNull()
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

  it('codex P2：AppBar 实测高度（>56 兜底）到达后重新钳制记忆位置', async () => {
    // 记忆位置 y=70：兜底 56 下合法（≥56），实测 AppBar 底边 100 下越界。
    window.localStorage.setItem(
      dockStorageKey('test-surface'),
      JSON.stringify({
        x: 900,
        y: 70,
        width: 520,
        height: 620,
        collapsed: false,
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
        collapsed: false,
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

  it('codex P2：Esc 折叠挂在 document 级——焦点在面板外（底层页面）也生效', async () => {
    render(
      (
        <div>
          <button type="button">底层按钮</button>
          <AgentPanelDock
            surfaceKey="test-surface"
            title="测试面板"
            onClose={() => undefined}
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
    expect(
      await screen.findByRole('button', { name: /已折叠，点击展开/ })
    ).toBeInTheDocument()
  })

  it('codex P2：有全局 MUI Modal 开着时 Esc 让给对方（不折叠 Dock）', async () => {
    render(
      (
        <div>
          <AgentPanelDock
            surfaceKey="test-surface"
            title="测试面板"
            onClose={() => undefined}
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
    // Dock 不折叠（Esc 属于模态）；小条不出现。
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(
      screen.queryByRole('button', { name: /已折叠，点击展开/ })
    ).toBeNull()
    expect(screen.getByRole('dialog', { name: '测试面板' })).toBeInTheDocument()
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
      expect(stored).toMatchObject({ x: 488, y: 56, collapsed: false })
    })
    expect(rndWrapper(surface).style.transform).toBe(
      jsdomTransform(488, 56, { x: 488, y: 64 })
    )
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

  it('codex P2 复审轮：拖拽改几何后按 Esc 折叠，写回存储的是新几何（Esc 回调不冻结首帧闭包）', async () => {
    renderDock()
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

    // Esc 折叠（document 级）：折叠写入不得把首帧几何（488,64）覆盖回去。
    fireEvent.keyDown(document.body, { key: 'Escape' })
    await screen.findByRole('button', { name: /已折叠，点击展开/ })
    expect(loadDockPlacement('test-surface')).toMatchObject({
      x: 288,
      y: 164,
      collapsed: true,
    })
  })

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
      // （见 jsdomTransform 注释）：报告 (y=0, height=620) → 钳后
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
        collapsed: false,
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
