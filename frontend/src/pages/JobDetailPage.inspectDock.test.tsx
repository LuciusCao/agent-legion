/**
 * JobDetailPage 的排查 Dock 接线测试（#795 PR③，姊妹文件——主测试文件贴近
 * 1000 行硬上限，Dock 用例独立成文）：头部「排查助手」开 job 级 Dock、失败
 * 节点入口同 Dock 带节点上下文、换节点 key 重挂、焦点归还给新触发按钮。
 * 脚手架（fetch mock / ActionRenderer / 面板 stub）形状与主测试文件一致。
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
  act,
} from '@testing-library/react'
import { Route, Routes, useNavigate } from 'react-router-dom'
import { MemoryRouter as ReactRouterMemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import JobDetailPage from './JobDetailPage'
import { useUiStore } from '../stores/uiStore'

// 排查面板 stub：带本地 state 的输入框作为「会话/composer 状态」的可观察
// 等价物（key 重挂断言），data-node-key 暴露上下文注入。
vi.mock('../features/jobDiagnosis/JobDiagnosisPanel', async () => {
  const { useState } = await import('react')
  return {
    JobDiagnosisPanel: function Stub({
      target,
    }: {
      workspaceId: string
      target: { jobId: string; nodeKey?: string | null }
    }) {
      const [text, setText] = useState('')
      return (
        <div
          data-testid="diagnosis-panel"
          data-job-id={target.jobId}
          data-node-key={target.nodeKey ?? ''}
        >
          <input
            aria-label="diagnosis-stub-input"
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
        </div>
      )
    },
  }
})

const mockDetail = {
  job: {
    id: 'j1',
    workspace_id: 'ws1',
    workflow_key: 'question_content',
    source_id: 'Q100',
    source_type: 'knowledge',
    title: 'Algebra Problem',
    status: 'failed',
    created_at: '2026-06-09T07:59:00Z',
    updated_at: '2026-06-09T08:00:00Z',
  },
  nodes: [
    {
      id: 1,
      job_id: 'j1',
      node_key: 'extract',
      label: '提取',
      status: 'completed',
      capability: 'extract',
      executor_id: 'code-default',
      executor_kind: 'code',
      after: [],
      inputs: [],
      outputs: [],
      started_at: '2026-06-09T08:00:00Z',
      finished_at: '2026-06-09T08:00:12Z',
      error_message: '',
    },
    {
      id: 2,
      job_id: 'j1',
      node_key: 'generate',
      label: '生成',
      status: 'failed',
      capability: 'generate',
      executor_id: 'pi',
      executor_kind: 'pi',
      after: ['extract'],
      inputs: [],
      outputs: [],
      started_at: '2026-06-09T08:00:13Z',
      error_message: 'boom',
    },
    {
      id: 3,
      job_id: 'j1',
      node_key: 'review',
      label: '审核',
      status: 'failed',
      capability: 'review',
      executor_id: 'pi-2',
      executor_kind: 'pi',
      after: ['generate'],
      inputs: [],
      outputs: [],
      started_at: '2026-06-09T08:01:00Z',
      error_message: 'boom2',
    },
  ],
  runs: [],
  artifacts: [],
}

// JobDetailPage injects app-bar actions into useUiStore, but WorkspaceLayout/AppBar
// is not rendered in this isolated test, so ActionRenderer renders the stored actions
// so tests can interact with them.
function ActionRenderer() {
  const actions = useUiStore((state) => state.detailPageActions)
  return <div data-testid="detail-actions-host">{actions}</div>
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/workspaces/ws1/jobs/j1']}>
      <ActionRenderer />
      <NavigateButton to="/workspaces/ws1/jobs/j1" label="去 j1" />
      <NavigateButton to="/workspaces/ws1/jobs/j2" label="去 j2" />
      <Routes>
        <Route
          path="/workspaces/:workspaceId/jobs/:jobId"
          element={<JobDetailPage />}
        />
      </Routes>
    </MemoryRouter>
  )
}

/** 带真实缓存的渲染（gcTime 默认 5min）——TestQueryProvider 的 gcTime:0
 * 会让每次切 job 都出 null 中间态，掩盖 #800 codex rebase 轮 P2 的
 * 「缓存命中 → signature 不变 → 操作栏不重写」路径。 */
function renderPageWithCache() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ReactRouterMemoryRouter
        initialEntries={['/workspaces/ws1/jobs/j1']}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <ActionRenderer />
        <NavigateButton to="/workspaces/ws1/jobs/j1" label="去 j1" />
        <NavigateButton to="/workspaces/ws1/jobs/j2" label="去 j2" />
        <Routes>
          <Route
            path="/workspaces/:workspaceId/jobs/:jobId"
            element={<JobDetailPage />}
          />
        </Routes>
      </ReactRouterMemoryRouter>
    </QueryClientProvider>
  )
}

function createFetchMock() {
  return vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    if (url === '/api/jobs/j1' && method === 'GET') {
      return Promise.resolve({ ok: true, json: async () => mockDetail })
    }
    if (url === '/api/jobs/j2' && method === 'GET') {
      // 与 j1 的 actionsSignature 其余维度全同（status/updated_at/节点数），
      // 只有 id/title 不同——钉 #800 codex rebase 轮 P2 的 stale 闭包场景。
      return Promise.resolve({
        ok: true,
        json: async () => ({
          ...mockDetail,
          job: { ...mockDetail.job, id: 'j2', title: 'Second Job' },
        }),
      })
    }
    return Promise.resolve({ ok: true, json: async () => ({}) })
  })
}

function NavigateButton({ to, label }: { to: string; label: string }) {
  const navigate = useNavigate()
  return (
    <button type="button" onClick={() => navigate(to)}>
      {label}
    </button>
  )
}

describe('JobDetailPage 排查 Dock（#795 PR③）', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    useUiStore.setState({ tokenUsageDialogOpen: false })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    cleanup()
  })

  it('opens the inspect dock from the header 排查助手 button with job-level context', async () => {
    vi.stubGlobal('fetch', createFetchMock())
    renderPage()
    await screen.findByText('提取')

    await act(async () => {
      screen.getByLabelText('排查助手').click()
    })
    // Dock 打开：非模态 surface（aria-modal=false），job 级目标（无节点）。
    expect(
      await screen.findByRole('dialog', { name: '排查：Algebra Problem' })
    ).toHaveAttribute('aria-modal', 'false')
    expect(screen.getByTestId('diagnosis-panel')).toHaveAttribute(
      'data-node-key',
      ''
    )

    // 关闭即卸载（与旧诊断弹窗语义等价：下次打开是全新排查会话）。
    await act(async () => {
      screen.getByRole('button', { name: '关闭' }).click()
    })
    await waitFor(() =>
      expect(
        screen.queryByRole('dialog', { name: '排查：Algebra Problem' })
      ).toBeNull()
    )
  })

  it('opens the same dock from a failed node with node context', async () => {
    vi.stubGlobal('fetch', createFetchMock())
    renderPage()
    await screen.findByText('提取')

    await act(async () => {
      screen.getAllByText('排查')[0].click()
    })
    expect(
      await screen.findByRole('dialog', { name: '排查：生成' })
    ).toBeInTheDocument()
    // 节点上下文注入（与旧弹窗 target 语义等价）。
    expect(screen.getByTestId('diagnosis-panel')).toHaveAttribute(
      'data-node-key',
      'generate'
    )
  })

  it('remounts the diagnosis subtree when reopened for another node (key 按 workspace+job+node)', async () => {
    // key 缺失时面板不 remount，stub 的本地 state 残留（revert 即红）。
    vi.stubGlobal('fetch', createFetchMock())
    renderPage()
    await screen.findByText('提取')

    const entries = screen.getAllByText('排查')
    await act(async () => {
      entries[0].click()
    })
    await screen.findByRole('dialog', { name: '排查：生成' })
    fireEvent.change(screen.getByLabelText('diagnosis-stub-input'), {
      target: { value: '未发送' },
    })
    expect(screen.getByLabelText('diagnosis-stub-input')).toHaveValue('未发送')

    await act(async () => {
      entries[1].click()
    })
    await screen.findByRole('dialog', { name: '排查：审核' })
    expect(screen.getByTestId('diagnosis-panel')).toHaveAttribute(
      'data-node-key',
      'review'
    )
    // 整棵重挂：旧节点会话的本地状态不带入。
    expect(screen.getByLabelText('diagnosis-stub-input')).toHaveValue('')
  })

  it('换节点重开后焦点归还给新触发按钮，不是旧节点的（#800 codex P2）', async () => {
    // key 重挂的卸载/挂载交错：旧 Dock 实例的清理先把焦点还给 A，若新
    // 实例读挂载时 activeElement 会错记 A——显式 restoreFocusRef 把归还
    // 目标绑定到唤起瞬间的触发元素（revert：还给 A，即红）。
    vi.stubGlobal('fetch', createFetchMock())
    renderPage()
    await screen.findByText('提取')
    const buttonA = screen.getAllByText('排查')[0].closest('button')!
    const buttonB = screen.getAllByText('排查')[1].closest('button')!

    // jsdom 的 click 不聚焦元素：显式聚焦模拟真实点击顺序。
    buttonA.focus()
    await act(async () => {
      buttonA.click()
    })
    await screen.findByRole('dialog', { name: '排查：生成' })

    buttonB.focus()
    await act(async () => {
      buttonB.click()
    })
    await screen.findByRole('dialog', { name: '排查：审核' })

    await act(async () => {
      screen.getByRole('button', { name: '关闭' }).click()
    })
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: /排查：/ })).toBeNull()
    )
    expect(document.activeElement).toBe(buttonB)
  })

  it('缓存内往返切 job 后，排查助手 primer 带当前页 jobId（#800 codex rebase 轮 P2）', async () => {
    // actionsSignature 不含 job.id 时：j2 已缓存（无 null 中间态）→ 切回
    // j1 signature 不变 → 操作栏不重写，「排查助手」仍是 j2 的闭包
    // （revert：primer 带 j2，即红）。TestQueryProvider 的 gcTime:0 造不出
    // 缓存命中路径，本用例用带真实缓存的渲染。
    vi.stubGlobal('fetch', createFetchMock())
    renderPageWithCache()
    await screen.findByText('提取')

    // 先访问 j2（入缓存），此处操作栏正常刷新为 j2。
    await act(async () => {
      screen.getByText('去 j2').click()
    })
    // 等 j2 内容渲染后再操作（加载中操作栏为空）。
    await screen.findByText('提取')
    await act(async () => {
      screen.getByLabelText('排查助手').click()
    })
    expect(
      await screen.findByRole('dialog', { name: '排查：Second Job' })
    ).toBeInTheDocument()
    await act(async () => {
      screen.getByRole('button', { name: '关闭' }).click()
    })

    // 缓存内切回 j1（无加载中间态）：操作栏必须随新 signature 重写。
    await act(async () => {
      screen.getByText('去 j1').click()
    })
    // 等 j1 内容渲染（缓存命中即时呈现；effect 随之刷新操作栏）。
    await screen.findByText('提取')
    await act(async () => {
      screen.getByLabelText('排查助手').click()
    })
    expect(
      await screen.findByRole('dialog', { name: '排查：Algebra Problem' })
    ).toBeInTheDocument()
    expect(screen.getByTestId('diagnosis-panel')).toHaveAttribute(
      'data-job-id',
      'j1'
    )
  })
})
