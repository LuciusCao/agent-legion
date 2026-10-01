/**
 * AgentPanelDock 焦点契约测试（姊妹文件——AgentPanelDock.test.tsx 近 800 行
 * 纪律线，焦点用例零改动迁入 + #797 复审轮 5 新增）：打开/展开进 surface、
 * 折叠到小条、卸载还原触发元素；hidden 归还面板外最后聚焦元素（focusin
 * 追踪）→ 指定选择器 → 挂载前元素的链；hidden 首次挂载不抢焦点。
 * localStorage stub / renderDock 形状与主测试文件一致。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { AgentPanelDock } from './AgentPanelDock'
import { expectConsoleError, expectConsoleWarning } from '../../test-setup'

// 该 jsdom 环境不提供 localStorage：用内存 stub（同主测试文件模式）。
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

beforeEach(() => {
  localStorageStub.clear()
})

describe('AgentPanelDock 焦点契约', () => {
  it('焦点移交：打开进面板、卸载还原触发元素（折叠态已移除，无 chip 移交）', async () => {
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

    unmount()
    expect(document.activeElement).toBe(trigger)
    trigger.remove()
  })

  it('codex P2（#797 复审轮 2）：hidden 时焦点还给面板外最后聚焦的控件（focusin 追踪），不掉进不可见子树', async () => {
    // 焦点移交 effect 驱动 MUI 状态更新脱离 act（known noise，同既有用例）。
    expectConsoleWarning(/not wrapped in act/)
    expectConsoleError(/not wrapped in act/)
    // 真实场景：常驻 Dock 已显示，用户点面板外控件（顶栏开关）再令
    // hidden=true——归还目标是「面板外最后聚焦的可见元素」（focusin 追踪），
    // 不是挂载前快照（常驻 Dock 挂载时通常是 body，已过期）。
    const trigger = document.createElement('button')
    document.body.appendChild(trigger)

    const { rerender } = renderDock(
      {},
      <input data-testid="dock-input" defaultValue="" />
    )
    const surface = await screen.findByRole('dialog', { name: '测试面板' })
    // Dock 可见期间：焦点先在面板内，再移到面板外的触发控件（focusin
    // 追踪记的是它），再回面板输入框。
    const input = screen.getByTestId('dock-input')
    input.focus()
    trigger.focus()
    input.focus()
    expect(document.activeElement).toBe(input)

    rerender(
      (
        <AgentPanelDock
          surfaceKey="test-surface"
          title="测试面板"
          onClose={() => undefined}
          hidden
        >
          <input data-testid="dock-input" defaultValue="" />
        </AgentPanelDock>
      ) as ReactElement
    )
    // hidden=true：焦点还给面板外最后聚焦的 trigger（不是 body、不是
    // display:none 子树——revert 即红）。
    await waitFor(() => expect(document.activeElement).toBe(trigger))

    // 恢复显示：焦点回 surface。
    rerender(
      (
        <AgentPanelDock
          surfaceKey="test-surface"
          title="测试面板"
          onClose={() => undefined}
          hidden={false}
        >
          <input data-testid="dock-input" defaultValue="" />
        </AgentPanelDock>
      ) as ReactElement
    )
    await waitFor(() => expect(document.activeElement).toBe(surface))
    trigger.remove()
  })

  it('codex P2（#797 复审轮 4）：首次关闭无面板外 focusin 时，焦点还给调用方指定的选择器目标', async () => {
    expectConsoleWarning(/not wrapped in act/)
    expectConsoleError(/not wrapped in act/)
    // 真实场景：桌面端 Dock 默认可见、挂载即把焦点拉进 surface；键盘用户
    // 直接 Tab 到关闭按钮关闭——全程无面板外 focusin，归还目标只能是
    // 调用方指定的选择器（顶栏开关/头部入口）。
    function Scenario({ hidden }: { hidden: boolean }) {
      return (
        <div>
          <button type="button" data-testid="dock-trigger">
            顶栏开关
          </button>
          <AgentPanelDock
            surfaceKey="test-surface"
            title="测试面板"
            onClose={() => undefined}
            hidden={hidden}
            restoreFocusSelector='[data-testid="dock-trigger"]'
          >
            <div>内容</div>
          </AgentPanelDock>
        </div>
      )
    }
    const { rerender } = render((<Scenario hidden={false} />) as ReactElement)
    await screen.findByRole('dialog', { name: '测试面板' })
    const closeButton = screen.getByRole('button', { name: '关闭' })
    closeButton.focus()
    expect(document.activeElement).toBe(closeButton)

    fireEvent.click(closeButton)
    rerender((<Scenario hidden />) as ReactElement)
    // 焦点落在顶栏开关（指定选择器），不是 body（revert 即红）。
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByTestId('dock-trigger'))
    )
  })

  it('codex P2（#797 复审轮 5）：hidden 首次挂载不抢焦点——仅可见→隐藏转换才归还', async () => {
    expectConsoleWarning(/not wrapped in act/)
    expectConsoleError(/not wrapped in act/)
    // 窄屏首进 studio 的形态：Dock 以 hidden=true 常驻挂载。用户焦点在页面
    // 别处（导航后的既有焦点）——归还 effect 初次挂载不得把焦点挪到顶栏
    // 开关（restoreTarget 选择器目标）。修复前（无「曾经可见」守卫）：焦点
    // 被抢到开关按钮（revert 即红）。
    const elsewhere = document.createElement('button')
    document.body.appendChild(elsewhere)
    elsewhere.focus()

    render(
      (
        <div>
          <button type="button" data-testid="dock-trigger">
            顶栏开关
          </button>
          <AgentPanelDock
            surfaceKey="test-surface"
            title="测试面板"
            onClose={() => undefined}
            hidden
            restoreFocusSelector='[data-testid="dock-trigger"]'
          >
            <div>内容</div>
          </AgentPanelDock>
        </div>
      ) as ReactElement
    )
    // 等挂载 effect 落定（Portal 内容已挂载，只是 display:none）。
    await screen.findByTestId('dock-trigger')
    expect(document.activeElement).toBe(elsewhere)
    elsewhere.remove()
  })

  it('codex 轮 8 P2（#797 复审批次）：归还目标被响应式 CSS 隐藏（display:none）时落到指定选择器兜底，不还给不可见元素', async () => {
    expectConsoleWarning(/not wrapped in act/)
    expectConsoleError(/not wrapped in act/)
    // 真实场景：窄屏 Agent 页签按钮是「面板外最后聚焦」的归还目标，断点
    // 切到宽屏后它被响应式 CSS display:none——isConnected 仍为 true，
    // focus 静默无效、焦点落 body，且不会继续尝试指定选择器。修复后归还
    // 链逐级校验可见性（revert：焦点还给 display:none 的页签按钮，即红）。
    function Scenario({ hidden }: { hidden: boolean }) {
      return (
        <div>
          <button type="button" data-testid="agent-tab">
            窄屏 Agent 页签
          </button>
          <button type="button" data-testid="dock-trigger">
            顶栏开关
          </button>
          <AgentPanelDock
            surfaceKey="test-surface"
            title="测试面板"
            onClose={() => undefined}
            hidden={hidden}
            restoreFocusSelector='[data-testid="dock-trigger"]'
          >
            <input data-testid="dock-input" defaultValue="" />
          </AgentPanelDock>
        </div>
      )
    }
    const { rerender } = render((<Scenario hidden={false} />) as ReactElement)
    await screen.findByRole('dialog', { name: '测试面板' })
    // Dock 可见期间 focusin 追踪记下面板外最后聚焦的页签按钮，然后焦点
    // 回到面板内。
    const tab = screen.getByTestId('agent-tab')
    tab.focus()
    screen.getByTestId('dock-input').focus()

    // 断点切换：页签按钮被响应式 CSS 隐藏（jsdom 无布局，用内联样式模拟
    // display:none——getComputedStyle 兜底链判得到）。
    tab.style.display = 'none'

    rerender((<Scenario hidden />) as ReactElement)
    // 焦点落到指定选择器（顶栏开关），不是 display:none 的页签按钮、不是
    // body。
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByTestId('dock-trigger'))
    )
  })

  it('codex 轮 9 P2（#797）：归还目标变禁用（disabled）时落到指定选择器兜底，不还给不可聚焦元素', async () => {
    expectConsoleWarning(/not wrapped in act/)
    expectConsoleError(/not wrapped in act/)
    // 真实场景：「面板外最后聚焦」的按钮（如发布按钮）在 Dock 打开期间变
    // 为禁用态——isConnected/可见性都还是 true，focus() 静默失败、焦点落
    // body，且旧链不继续尝试指定选择器。修复后禁用目标被排除 + focus 后
    // 验证双保险（revert：焦点还给 disabled 按钮或落 body，即红）。
    function Scenario({ hidden }: { hidden: boolean }) {
      return (
        <div>
          <button type="button" data-testid="publish-button">
            发布
          </button>
          <button type="button" data-testid="dock-trigger">
            顶栏开关
          </button>
          <AgentPanelDock
            surfaceKey="test-surface"
            title="测试面板"
            onClose={() => undefined}
            hidden={hidden}
            restoreFocusSelector='[data-testid="dock-trigger"]'
          >
            <input data-testid="dock-input" defaultValue="" />
          </AgentPanelDock>
        </div>
      )
    }
    const { rerender } = render((<Scenario hidden={false} />) as ReactElement)
    await screen.findByRole('dialog', { name: '测试面板' })
    const publish = screen.getByTestId('publish-button') as HTMLButtonElement
    publish.focus()
    screen.getByTestId('dock-input').focus()

    // Dock 打开期间目标变禁用。
    publish.disabled = true

    rerender((<Scenario hidden />) as ReactElement)
    // 焦点落到指定选择器（顶栏开关），不是 disabled 按钮、不是 body。
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByTestId('dock-trigger'))
    )

    // 正路径不误判：目标恢复可用后，归还链仍优先还给它（focus 后验证
    // 不排斥正常目标）。
    rerender((<Scenario hidden={false} />) as ReactElement)
    await screen.findByRole('dialog', { name: '测试面板' })
    publish.disabled = false
    publish.focus()
    screen.getByTestId('dock-input').focus()
    rerender((<Scenario hidden />) as ReactElement)
    await waitFor(() => expect(document.activeElement).toBe(publish))
  })

  it('restoreFocusRef 显式指定的归还目标优先于挂载时焦点（#800 codex P2）', async () => {
    expectConsoleWarning(/not wrapped in act/)
    expectConsoleError(/not wrapped in act/)
    // 挂载时焦点在别处（模拟换目标重挂：旧实例清理刚把焦点还给旧触发
    // 元素），但归还目标以调用方在唤起瞬间记录的 ref 为准（revert：焦点
    // 落到挂载前的 other，即红）。
    const designated = document.createElement('button')
    const other = document.createElement('button')
    document.body.append(designated, other)
    const restoreFocusRef = { current: designated }
    other.focus()

    const { unmount } = renderDock({ restoreFocusRef })
    await screen.findByRole('dialog', { name: '测试面板' })

    unmount()
    expect(document.activeElement).toBe(designated)
    designated.remove()
    other.remove()
  })
})
