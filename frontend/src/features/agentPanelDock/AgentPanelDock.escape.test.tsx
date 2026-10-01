/**
 * AgentPanelDock 的 Esc 让位契约测试（#797 复审批次，姊妹文件——主测试
 * 文件已贴近 800 行纪律线，Esc 用例独立成文）：
 * - 非 MUI 顶层浮层消费 Esc：ArtifactPopover（capture 阶段 preventDefault）
 *   与 DagFullscreenDialog（role=dialog aria-modal=true 的通用让位）开着时
 *   Esc 不关闭 Dock；
 * - IME 组字中的 Esc 是取消候选，不关闭。
 * #795 收尾：Esc 语义从折叠改为关闭（走 onClose），断言相应改读 onClose。
 * localStorage stub 形状与主测试文件一致。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  render,
  screen,
  fireEvent,
  within,
  waitFor,
} from '@testing-library/react'
import { useState } from 'react'
import type { ReactElement } from 'react'
import { AgentPanelDock } from './AgentPanelDock'
import { ArtifactPopover } from '../../components/artifact/ArtifactPopover'
import { DagFullscreenDialog } from '../../components/dag/DagFullscreenDialog'

vi.mock('../../api/jobApi')

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

beforeEach(() => {
  localStorageStub.clear()
})

describe('AgentPanelDock Esc 让位（#797 复审批次）', () => {
  it('ArtifactPopover 打开时 Esc 只关气泡、不关闭 Dock（capture 阶段消费，无双重消费）', async () => {
    // 真实场景：DAG 节点产物气泡与 Dock 同开；气泡的 Esc 监听同挂
    // document 且注册更晚，bubble 阶段 stopPropagation/preventDefault 拦不
    // 住 Dock——修复前同一击键把 Dock 也关掉（revert 即红）。
    const onPopoverClose = vi.fn()
    const onDockClose = vi.fn()
    render(
      (
        <div>
          <AgentPanelDock
            surfaceKey="test-surface"
            title="测试面板"
            onClose={onDockClose}
          >
            <div>内容</div>
          </AgentPanelDock>
          <ArtifactPopover items={['a.json']} onClose={onPopoverClose} />
        </div>
      ) as ReactElement
    )
    await screen.findByRole('dialog', { name: '测试面板' })
    const popover = screen.getByRole('dialog', { name: '产物列表' })

    // 击键落在气泡内（气泡挂载时焦点已进它的按钮）。
    fireEvent.keyDown(within(popover).getByRole('button', { name: '关闭' }), {
      key: 'Escape',
    })

    await waitFor(() => expect(onPopoverClose).toHaveBeenCalledTimes(1))
    // Dock 不关闭：onClose 不触发、surface 仍在。
    expect(onDockClose).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog', { name: '测试面板' })).toBeInTheDocument()
  })

  it('全屏 DAG（role=dialog aria-modal=true 的非 MUI 浮层）打开时 Esc 让给全屏层，不关闭 Dock', async () => {
    // 真实场景：job detail 全屏 DAG（z 1000 > Dock 900）盖住 Dock 时按
    // Esc，意图作用于全屏层——让位判定不绑死 MUI 类名（revert：只认
    // .MuiModal-root → Dock 被关闭，即红）。
    const onDockClose = vi.fn()
    render(
      (
        <div>
          <AgentPanelDock
            surfaceKey="test-surface"
            title="测试面板"
            onClose={onDockClose}
          >
            <div>内容</div>
          </AgentPanelDock>
          <DagFullscreenDialog
            open
            jobId="job-1"
            nodes={[]}
            edges={[]}
            onClose={() => undefined}
          />
        </div>
      ) as ReactElement
    )
    await screen.findByRole('dialog', { name: '测试面板' })
    await screen.findByRole('dialog', { name: 'DAG 视图' })

    fireEvent.keyDown(document.body, { key: 'Escape' })
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(onDockClose).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog', { name: '测试面板' })).toBeInTheDocument()
  })

  it('IME 组字中的 Esc 是取消候选，不关闭 Dock（与 composer Enter 守卫同款）', async () => {
    const onDockClose = vi.fn()
    render(
      (
        <AgentPanelDock
          surfaceKey="test-surface"
          title="测试面板"
          onClose={onDockClose}
        >
          <div>内容</div>
        </AgentPanelDock>
      ) as ReactElement
    )
    const surface = await screen.findByRole('dialog', { name: '测试面板' })

    fireEvent.keyDown(surface, { key: 'Escape', isComposing: true })
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(onDockClose).not.toHaveBeenCalled()

    // 组合结束后的 Esc 照常关闭（守卫不误伤正常路径）。
    fireEvent.keyDown(surface, { key: 'Escape', isComposing: false })
    expect(onDockClose).toHaveBeenCalledTimes(1)
  })
})

describe('AgentPanelDock Esc 栈（#801 codex P1：同页多实例只关栈顶）', () => {
  function renderTwoDocks() {
    const closeA = vi.fn()
    const closeB = vi.fn()
    // onClose 走真实语义（卸载/隐藏出栈）：关闭的 Dock 不再占栈。
    function TwoDocks() {
      const [openA, setOpenA] = useState(true)
      const [openB, setOpenB] = useState(true)
      return (
        <div>
          {openA && (
            <AgentPanelDock
              surfaceKey="dock-a"
              title="面板 A"
              onClose={() => {
                closeA()
                setOpenA(false)
              }}
            >
              <div>内容 A</div>
            </AgentPanelDock>
          )}
          {openB && (
            <AgentPanelDock
              surfaceKey="dock-b"
              title="面板 B"
              onClose={() => {
                closeB()
                setOpenB(false)
              }}
            >
              <div>内容 B</div>
            </AgentPanelDock>
          )}
        </div>
      )
    }
    render((<TwoDocks />) as ReactElement)
    return { closeA, closeB }
  }

  it('Esc 只关栈顶（最后挂载的）；栈顶关闭后下一个成为栈顶', async () => {
    // revert（无栈判定）：一次 Esc 两个 onClose 都触发，即红。
    const { closeA, closeB } = renderTwoDocks()
    await screen.findByRole('dialog', { name: '面板 A' })
    await screen.findByRole('dialog', { name: '面板 B' })

    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(closeB).toHaveBeenCalledTimes(1)
    expect(closeA).not.toHaveBeenCalled()

    // 栈顶（B）关闭后 A 成为栈顶，再 Esc 关它。
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(closeA).toHaveBeenCalledTimes(1)
    expect(closeB).toHaveBeenCalledTimes(1)
  })

  it('交互（pointerdown/焦点进入）把 Dock 抬为栈顶', async () => {
    const { closeA, closeB } = renderTwoDocks()
    const surfaceA = await screen.findByRole('dialog', { name: '面板 A' })
    await screen.findByRole('dialog', { name: '面板 B' })

    // 点击 A 的内容区（B 是栈顶）→ A 置顶 → Esc 关 A 不关 B。
    fireEvent.pointerDown(surfaceA)
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(closeA).toHaveBeenCalledTimes(1)
    expect(closeB).not.toHaveBeenCalled()
  })

  it('交互抬栈同步到视觉层级（z-index = 900 + 栈位，#801 codex 轮 2）', async () => {
    // revert（Rnd 固定 zIndex 900）：点击 A 后两个 Dock 层级相同，即红。
    renderTwoDocks()
    const surfaceA = await screen.findByRole('dialog', { name: '面板 A' })
    const surfaceB = await screen.findByRole('dialog', { name: '面板 B' })
    const wrapperA = surfaceA.parentElement as HTMLElement
    const wrapperB = surfaceB.parentElement as HTMLElement
    // 初始：B 后挂载为栈顶，层级更高。
    expect(Number(wrapperB.style.zIndex)).toBeGreaterThan(
      Number(wrapperA.style.zIndex)
    )

    // 点击 A 的露出区域：A 抬为栈顶，视觉层级同步反超 B。
    fireEvent.pointerDown(surfaceA)
    await waitFor(() =>
      expect(Number(wrapperA.style.zIndex)).toBeGreaterThan(
        Number(wrapperB.style.zIndex)
      )
    )
    // 上限契约：栈位映射的 z-index 永远低于 Toast 1000。
    expect(Number(wrapperA.style.zIndex)).toBeLessThan(1000)
  })

  it('缩放开始也抬栈（#801 codex 轮 4 P2：resize 把手在 Paper 外，pointerdown capture 摸不到）', async () => {
    // revert（onResizeStart 不抬栈）：对下层 Dock 的把手 mouseDown 后它的
    // 层级不反超，即红。
    renderTwoDocks()
    const surfaceA = await screen.findByRole('dialog', { name: '面板 A' })
    const surfaceB = await screen.findByRole('dialog', { name: '面板 B' })
    const wrapperA = surfaceA.parentElement as HTMLElement
    const wrapperB = surfaceB.parentElement as HTMLElement
    expect(Number(wrapperB.style.zIndex)).toBeGreaterThan(
      Number(wrapperA.style.zIndex)
    )

    // re-resizable 的把手无默认类名，按内联 style 识别（同既有缩放用例）。
    const handleA = Array.from(
      wrapperA.querySelectorAll('div[style*="col-resize"]')
    )[0] as HTMLElement
    expect(handleA).toBeTruthy()
    fireEvent.mouseDown(handleA, { clientX: 900, clientY: 400 })
    await waitFor(() =>
      expect(Number(wrapperA.style.zIndex)).toBeGreaterThan(
        Number(wrapperB.style.zIndex)
      )
    )
    fireEvent.mouseUp(document, { clientX: 900, clientY: 400 })
  })
})
