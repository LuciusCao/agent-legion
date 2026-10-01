/**
 * JobInspectDock（#795 PR③）契约测试：AgentPanelDock + JobDiagnosisPanel 薄
 * 组合——标题取自排查目标（节点名 > job 标题 > jobId）、target 原样注入
 * 面板、关闭按钮回调、Esc 关闭（#795 收尾：折叠态移除）、卸载焦点归还
 * 触发元素（AgentPanelDock 基座契约）。
 * localStorage stub 与 stateful 面板 stub 模式同 agentPanelDock 测试。
 */
import { useState } from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { JobInspectDock } from './JobInspectDock'
import type { JobDiagnosisTarget } from './jobDiagnosisContext'
import { expectConsoleError, expectConsoleWarning } from '../../test-setup'

// 面板 stub：带本地 state（页面级重挂断言的可观察等价物）+ 记录收到的
// target 与 inDock（上下文注入与 Dock 外壳变体断言）。
const stubProps: { target: JobDiagnosisTarget; inDock?: boolean }[] = []
vi.mock('./JobDiagnosisPanel', async () => {
  const { useState: useStateInner } = await import('react')
  return {
    JobDiagnosisPanel: function Stub({
      target,
      inDock,
    }: {
      workspaceId: string
      target: JobDiagnosisTarget
      inDock?: boolean
    }) {
      stubProps.push({ target, inDock })
      const [text, setText] = useStateInner('')
      return (
        <input
          data-testid="diagnosis-stub-input"
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
      )
    },
  }
})

// 该 jsdom 环境不提供 localStorage：用内存 stub（Dock 按 surface key 记忆
// 位置）。
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

const baseTarget: JobDiagnosisTarget = {
  workspaceId: 'ws1',
  jobId: 'j1',
  jobTitle: 'Algebra Problem',
}

function renderDock(
  target: JobDiagnosisTarget = baseTarget,
  onClose = () => undefined
) {
  return render(
    (<JobInspectDock target={target} onClose={onClose} />) as ReactElement
  )
}

beforeEach(() => {
  localStorageStub.clear()
  stubProps.length = 0
})

describe('JobInspectDock（#795 PR③：排查走 AgentPanelDock）', () => {
  it('节点级目标：标题带节点名，target（含 nodeKey/nodeLabel）原样注入面板', async () => {
    renderDock({ ...baseTarget, nodeKey: 'generate', nodeLabel: '生成' })
    expect(
      await screen.findByRole('dialog', { name: '排查：生成' })
    ).toBeInTheDocument()
    const last = stubProps[stubProps.length - 1]
    expect(last.target).toEqual({
      workspaceId: 'ws1',
      jobId: 'j1',
      jobTitle: 'Algebra Problem',
      nodeKey: 'generate',
      nodeLabel: '生成',
    })
    // Dock 宿主变体（#800 codex P2）：不带旧 Dialog 的 320px 底尺寸。
    expect(last.inDock).toBe(true)
    // 无记忆时默认几何 = 新默认尺寸（#795 收尾：520×640 → ×1.1 = 572×704）；
    // jsdom 视口 768 高：高度被钳到 768-56-32=680（#797 轮 7 上限封顶语义）。
    const wrapper = screen.getByRole('dialog', { name: '排查：生成' })
      .parentElement as HTMLElement
    expect(wrapper.style.width).toBe('572px')
    expect(wrapper.style.height).toBe('680px')
  })

  it('job 级目标：标题用 job 标题，无 jobTitle 回退 jobId', async () => {
    renderDock(baseTarget)
    expect(
      await screen.findByRole('dialog', { name: '排查：Algebra Problem' })
    ).toBeInTheDocument()
    expect(stubProps[stubProps.length - 1]?.target.nodeKey).toBeUndefined()

    const { unmount } = renderDock({ workspaceId: 'ws1', jobId: 'j2' })
    expect(
      await screen.findByRole('dialog', { name: '排查：j2' })
    ).toBeInTheDocument()
    unmount()
  })

  it('Esc 关闭走 onClose（#795 收尾：折叠态移除，Esc 与关闭按钮同路径）', async () => {
    expectConsoleWarning(/not wrapped in act/)
    expectConsoleError(/not wrapped in act/)
    const onClose = vi.fn()
    renderDock(baseTarget, onClose)
    const surface = await screen.findByRole('dialog', {
      name: '排查：Algebra Problem',
    })
    fireEvent.keyDown(surface, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
    // 标题栏只有关闭按钮，无折叠 chip。
    expect(screen.queryByRole('button', { name: '折叠面板' })).toBeNull()
    expect(screen.queryByRole('button', { name: /已折叠/ })).toBeNull()
  })

  it('关闭按钮回调 onClose；卸载后焦点归还触发元素', async () => {
    expectConsoleWarning(/not wrapped in act/)
    expectConsoleError(/not wrapped in act/)
    // 真实流程：点击「排查助手」打开 Dock（点击即聚焦触发按钮——Dock 挂载
    // 时记录的归还目标就是它）。
    function Scenario() {
      const [open, setOpen] = useState(false)
      return (
        <div>
          <button
            type="button"
            data-testid="trigger"
            onClick={() => setOpen(true)}
          >
            排查助手
          </button>
          {open && (
            <JobInspectDock
              target={baseTarget}
              onClose={() => setOpen(false)}
            />
          )}
        </div>
      )
    }
    render((<Scenario />) as ReactElement)
    const trigger = screen.getByTestId('trigger')
    // fireEvent.click 不聚焦元素（jsdom）：显式聚焦，模拟真实点击的聚焦
    // 顺序——Dock 挂载时记录的归还目标就是它。
    trigger.focus()
    fireEvent.click(trigger)

    await screen.findByRole('dialog', { name: '排查：Algebra Problem' })
    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    await waitFor(() =>
      expect(
        screen.queryByRole('dialog', { name: '排查：Algebra Problem' })
      ).toBeNull()
    )
    expect(document.activeElement).toBe(trigger)
  })
})
