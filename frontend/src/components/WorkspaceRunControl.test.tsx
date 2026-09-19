import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import { WorkspaceRunControl } from './WorkspaceRunControl'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import { useConnectionStatusStore } from '../stores/connectionStatusStore'
import type { AgentWorkerSummary as WorkerSummary } from '../api/agentWorkers'
import { formatDateTime } from '../lib/formatters'
import { createMockAgentsState, createMockUiState } from '../testing/fixtures'
import { makeAgentStatus } from '../testing/workspaceFixtures'
import type { AgentStatus } from '../types'

function renderControl() {
  return render(
    <MemoryRouter>
      <WorkspaceRunControl workspaceId="ws1" />
    </MemoryRouter>
  )
}

const fetchWorkerStatusMock = vi.fn()
const setWorkerPausedMock = vi.fn()
const showToastMock = vi.fn()
const listAgentWorkersMock = vi.fn()

vi.mock('../api/agentWorkers', () => ({
  listAgentWorkers: () => listAgentWorkersMock(),
  fetchAgentWorkers: () =>
    Promise.resolve({ workers: [], console_url: 'http://127.0.0.1:8789' }),
}))

vi.mock('../hooks/useWorkerConsoleUrl', () => ({
  useWorkerConsoleUrl: () => 'http://127.0.0.1:8789',
}))

function makeWorker(overrides: Partial<WorkerSummary> = {}): WorkerSummary {
  return {
    worker_id: 'worker-1',
    name: 'Company Mac',
    runtimes: ['pi'],
    capabilities: ['review_subtitles'],
    models: [{ provider: 'openai', model: 'gpt-5.2' }],
    max_concurrency: 10,
    max_code_concurrency: 0,
    labels: {},
    protocol_version: 1,
    registered_at: '2026-07-22 02:13:04',
    last_seen_at: '2026-07-22 02:15:31',
    online: true,
    revoked: false,
    allowed_workspaces: [],
    ...overrides,
  }
}

let mockWorkerPausedByWorkspace: Record<string, boolean> = {}
let mockAgents: AgentStatus[] = [
  makeAgentStatus({
    id: 'main',
    name: 'Main',
    workspace_id: 'ws1',
    max_tasks: 8,
  }),
]

vi.mock('../stores/agentsStore', () => ({
  useAgentsStore: (
    selector?: (state: ReturnType<typeof createMockAgentsState>) => unknown
  ) => {
    const state = createMockAgentsState({
      workerPausedByWorkspace: mockWorkerPausedByWorkspace,
      agents: mockAgents,
      getWorkerPaused: (workspaceId: string) =>
        mockWorkerPausedByWorkspace[workspaceId] ?? true,
      fetchWorkerStatus: fetchWorkerStatusMock,
      setWorkerPaused: setWorkerPausedMock,
    })
    return selector ? selector(state) : state
  },
}))

vi.mock('../stores/uiStore', () => ({
  useUiStore: (
    selector?: (state: ReturnType<typeof createMockUiState>) => unknown
  ) => {
    const state = createMockUiState({
      showToast: showToastMock,
    })
    return selector ? selector(state) : state
  },
}))

describe('WorkspaceRunControl', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockWorkerPausedByWorkspace = {}
    useConnectionStatusStore.setState({ connectionStatus: {} })
    listAgentWorkersMock.mockResolvedValue([])
    mockAgents = [
      makeAgentStatus({
        id: 'main',
        name: 'Main',
        workspace_id: 'ws1',
        max_tasks: 8,
      }),
    ]
    fetchWorkerStatusMock.mockResolvedValue(undefined)
    setWorkerPausedMock.mockResolvedValue(undefined)
  })

  it('renders run state button', () => {
    renderControl()
    expect(screen.getByLabelText('恢复运行')).toBeInTheDocument()
  })

  it('shows 已暂停 when the workspace is paused', () => {
    mockWorkerPausedByWorkspace = { ws1: true }
    renderControl()
    expect(screen.getByText('已暂停')).toBeInTheDocument()
  })

  it('shows 运行中 when the workspace is running', () => {
    mockWorkerPausedByWorkspace = { ws1: false }
    renderControl()
    expect(screen.getByText('运行中')).toBeInTheDocument()
  })

  it('fetches worker status for the given workspace on mount', () => {
    renderControl()
    expect(fetchWorkerStatusMock).toHaveBeenCalledWith('ws1')
  })

  it('popover is display-only: no switch, only the worker list', () => {
    renderControl()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.getByText('已注册 Worker')).toBeInTheDocument()
  })

  it('resumes running when the button is clicked while paused', async () => {
    mockWorkerPausedByWorkspace = { ws1: true }
    renderControl()

    await act(async () => {
      fireEvent.click(screen.getByLabelText('恢复运行'))
    })

    expect(setWorkerPausedMock).toHaveBeenCalledWith(false, 'ws1')
    expect(showToastMock).toHaveBeenCalledWith('已恢复运行', 'success')
  })

  it('pauses running when the button is clicked while running', async () => {
    mockWorkerPausedByWorkspace = { ws1: false }
    renderControl()

    await act(async () => {
      fireEvent.click(screen.getByLabelText('暂停运行'))
    })

    expect(setWorkerPausedMock).toHaveBeenCalledWith(true, 'ws1')
    expect(showToastMock).toHaveBeenCalledWith('已暂停运行', 'success')
  })

  it('shows workspace-specific agents', () => {
    mockAgents = [
      makeAgentStatus({
        id: 'main',
        name: 'Main',
        workspace_id: 'ws1',
        max_tasks: 8,
      }),
      makeAgentStatus({
        id: 'pi',
        name: 'Pi Agent',
        workspace_id: 'ws2',
        max_tasks: 2,
      }),
    ]
    renderControl()
    expect(screen.getByText('Main')).toBeInTheDocument()
    expect(screen.queryByText('Pi Agent')).not.toBeInTheDocument()
  })

  it('shows worker busy count over capacity', () => {
    mockAgents = [
      makeAgentStatus({
        id: 'mac-mini',
        name: 'MacMini',
        workspace_id: 'ws1',
        busy: true,
        task_count: 3,
        max_tasks: 16,
      }),
    ]
    renderControl()
    expect(screen.getByText('MacMini')).toBeInTheDocument()
    expect(screen.getByText('忙碌 3/16')).toBeInTheDocument()
  })

  it('shows empty state with a Worker console entry when no worker is available', () => {
    mockAgents = []
    renderControl()
    expect(screen.getByText(/暂无可用 Worker/)).toBeInTheDocument()
    // 空态直接把人送到 Worker 控制台（添加 Key、开始领取都在那边）。
    expect(screen.getByTestId('worker-console-link')).toHaveAttribute(
      'href',
      'http://127.0.0.1:8789'
    )
  })

  it('shows a disconnected status dot when the agents channel is closed', () => {
    useConnectionStatusStore.setState({
      connectionStatus: { agents: 'closed' },
    })
    renderControl()
    const dot = screen.getByTestId('agents-connection-status')
    expect(dot).toBeInTheDocument()
    expect(dot).toHaveAttribute('title', 'Agent 连接已断开')
  })

  it('shows a connecting status dot when the agents channel is connecting', () => {
    useConnectionStatusStore.setState({
      connectionStatus: { agents: 'connecting' },
    })
    renderControl()
    const dot = screen.getByTestId('agents-connection-status')
    expect(dot).toBeInTheDocument()
    expect(dot).toHaveAttribute('title', 'Agent 连接中')
  })

  it('hides the status dot when the agents channel is open', () => {
    useConnectionStatusStore.setState({ connectionStatus: { agents: 'open' } })
    renderControl()
    expect(
      screen.queryByTestId('agents-connection-status')
    ).not.toBeInTheDocument()
  })

  it('fetches registered workers on mount', async () => {
    renderControl()
    await waitFor(() => expect(listAgentWorkersMock).toHaveBeenCalled())
  })

  it('shows online and offline chips with last-seen heartbeat', async () => {
    listAgentWorkersMock.mockResolvedValue([
      makeWorker({ worker_id: 'w-online', name: 'Online Mac', online: true }),
      makeWorker({
        worker_id: 'w-offline',
        name: 'Offline Mac',
        online: false,
        last_seen_at: '2026-07-22 01:00:00',
      }),
    ])
    renderControl()
    expect(screen.getByText('已注册 Worker')).toBeInTheDocument()
    await screen.findByText('Online Mac')
    expect(screen.getByText('Offline Mac')).toBeInTheDocument()
    expect(
      screen.getByTitle(`最近心跳 ${formatDateTime('2026-07-22 02:15:31')}`)
    ).toHaveTextContent('在线')
    expect(
      screen.getByTitle(`最近心跳 ${formatDateTime('2026-07-22 01:00:00')}`)
    ).toHaveTextContent('离线')
  })

  it('filters workers by allowed workspaces; empty list means all workspaces', async () => {
    listAgentWorkersMock.mockResolvedValue([
      makeWorker({ worker_id: 'w-global', name: 'Global Mac' }),
      makeWorker({
        worker_id: 'w-scoped',
        name: 'Scoped Mac',
        allowed_workspaces: ['ws1'],
      }),
      makeWorker({
        worker_id: 'w-other',
        name: 'Other Mac',
        allowed_workspaces: ['ws2'],
      }),
    ])
    renderControl()
    await screen.findByText('Global Mac')
    expect(screen.getByText('Scoped Mac')).toBeInTheDocument()
    expect(screen.queryByText('Other Mac')).not.toBeInTheDocument()
  })

  it('flags an online worker whose claim switch is off', async () => {
    listAgentWorkersMock.mockResolvedValue([
      makeWorker({
        worker_id: 'w-idle',
        name: 'Idle Mac',
        claim_enabled: false,
      }),
      makeWorker({
        worker_id: 'w-busy',
        name: 'Busy Mac',
        claim_enabled: true,
      }),
    ])
    renderControl()
    await screen.findByText('Idle Mac')
    // v83：领取开关关闭的 Worker 不再是普通「在线」，悬停先讲怎么修。
    const idle = screen.getByText('在线·未领取')
    expect(idle).toHaveAttribute('title', expect.stringContaining('开始领取'))
    expect(screen.getByText('在线·领取中')).toBeInTheDocument()
  })

  it('links each registered worker row to its self-reported console', async () => {
    listAgentWorkersMock.mockResolvedValue([
      makeWorker({
        worker_id: 'w-a',
        name: 'Mac A',
        labels: { console_url: 'http://10.0.0.8:8787' },
      }),
      makeWorker({ worker_id: 'w-b', name: 'Mac B' }),
    ])
    renderControl()
    await screen.findByText('Mac A')
    // 只有自报了地址（labels.console_url）的 Worker 行才有入口；旧版 Worker 没有。
    const links = screen.getAllByTestId('worker-console-link')
    expect(links).toHaveLength(1)
    expect(links[0]).toHaveAttribute('href', 'http://10.0.0.8:8787')
    expect(links[0]).toHaveTextContent('控制台')
  })

  it('does not show revoked workers', async () => {
    listAgentWorkersMock.mockResolvedValue([
      makeWorker({
        worker_id: 'w-revoked',
        name: 'Revoked Mac',
        revoked: true,
      }),
    ])
    renderControl()
    // 等 workers 查询 resolve 后再断言缺失，避免抢在数据到达之前。
    await act(async () => {})
    expect(screen.queryByText('Revoked Mac')).not.toBeInTheDocument()
    // Local agent rows without a registered Worker still render.
    expect(screen.getByText('Main')).toBeInTheDocument()
  })

  it('merges registered worker info and workspace workload into one row', async () => {
    listAgentWorkersMock.mockResolvedValue([
      makeWorker({
        worker_id: 'mac-air',
        name: 'MacbookAir',
        online: true,
        max_concurrency: 30,
      }),
    ])
    mockAgents = [
      makeAgentStatus({
        id: 'mac-air',
        name: 'MacbookAir',
        workspace_id: 'ws1',
        busy: true,
        task_count: 3,
        max_tasks: 30,
      }),
    ]
    renderControl()
    await screen.findByText('在线')
    expect(screen.getAllByText('MacbookAir')).toHaveLength(1)
    expect(screen.getByText('忙碌 3/30')).toBeInTheDocument()
  })

  it('falls back to worker capacity when no workload row exists yet', async () => {
    listAgentWorkersMock.mockResolvedValue([
      makeWorker({
        worker_id: 'w-idle',
        name: 'Idle Mac',
        online: true,
        max_concurrency: 10,
      }),
    ])
    mockAgents = []
    renderControl()
    await screen.findByText('Idle Mac')
    expect(screen.getByText('空闲 0/10')).toBeInTheDocument()
  })
})
