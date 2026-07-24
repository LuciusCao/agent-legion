import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { AgentStatusIndicator } from './AgentStatusIndicator'
import { useExecutorsStore, type WorkerSummary } from '../stores/executorsStore'
import { createMockUiState } from '../testing/fixtures'
import { makeAgentStatus } from '../testing/workspaceFixtures'
import type { AgentStatus } from '../types'

const fetchWorkerStatusMock = vi.fn()
const setWorkerPausedMock = vi.fn()
const showToastMock = vi.fn()
const refreshWorkersMock = vi.fn()

function makeWorker(overrides: Partial<WorkerSummary> = {}): WorkerSummary {
  return {
    worker_id: 'worker-1',
    name: 'Company Mac',
    runtimes: ['pi'],
    capabilities: ['review_subtitles'],
    models: [{ provider: 'openai', model: 'gpt-5.2' }],
    max_concurrency: 10,
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

vi.mock('../stores/uiStore', () => ({
  useUiStore: (
    selector?: (state: ReturnType<typeof createMockUiState>) => unknown
  ) => {
    const state = createMockUiState({
      workerPausedByWorkspace: mockWorkerPausedByWorkspace,
      agents: mockAgents,
      getWorkerPaused: (workspaceId: string) =>
        mockWorkerPausedByWorkspace[workspaceId] ?? true,
      fetchWorkerStatus: fetchWorkerStatusMock,
      setWorkerPaused: setWorkerPausedMock,
      showToast: showToastMock,
    })
    return selector ? selector(state) : state
  },
}))

describe('AgentStatusIndicator', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockWorkerPausedByWorkspace = {}
    useExecutorsStore.setState({
      workers: [],
      connectionStatus: {},
      refreshWorkers: refreshWorkersMock,
    })
    refreshWorkersMock.mockResolvedValue(undefined)
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

  it('renders agent status button', () => {
    render(<AgentStatusIndicator workspaceId="ws1" />)
    expect(screen.getByLabelText('Agent 状态')).toBeInTheDocument()
  })

  it('fetches worker status for the given workspace on mount', () => {
    render(<AgentStatusIndicator workspaceId="ws1" />)
    expect(fetchWorkerStatusMock).toHaveBeenCalledWith('ws1')
  })

  it('shows auto-scheduling switch in the popover', () => {
    render(<AgentStatusIndicator workspaceId="ws1" />)
    expect(screen.getByText('自动调度')).toBeInTheDocument()
  })

  it('resumes scheduling when switch is toggled on', async () => {
    mockWorkerPausedByWorkspace = { ws1: true }
    render(<AgentStatusIndicator workspaceId="ws1" />)
    const switchEl = screen.getByRole('checkbox')
    expect(switchEl).toBeInTheDocument()

    await act(async () => {
      fireEvent.click(switchEl)
    })

    expect(setWorkerPausedMock).toHaveBeenCalledWith(false, 'ws1')
    expect(showToastMock).toHaveBeenCalledWith('已恢复自动调度', 'success')
  })

  it('pauses scheduling when switch is toggled off', async () => {
    mockWorkerPausedByWorkspace = { ws1: false }
    render(<AgentStatusIndicator workspaceId="ws1" />)
    const switchEl = screen.getByRole('checkbox')
    expect(switchEl).toBeInTheDocument()

    await act(async () => {
      fireEvent.click(switchEl)
    })

    expect(setWorkerPausedMock).toHaveBeenCalledWith(true, 'ws1')
    expect(showToastMock).toHaveBeenCalledWith('已暂停自动调度', 'success')
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
    render(<AgentStatusIndicator workspaceId="ws1" />)
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
    render(<AgentStatusIndicator workspaceId="ws1" />)
    expect(screen.getByText('MacMini')).toBeInTheDocument()
    expect(screen.getByText('忙碌 3/16')).toBeInTheDocument()
  })

  it('shows empty state when no worker is available', () => {
    mockAgents = []
    render(<AgentStatusIndicator workspaceId="ws1" />)
    expect(screen.getByText('暂无可用 Worker')).toBeInTheDocument()
  })

  it('shows a disconnected status dot when the agents channel is closed', () => {
    useExecutorsStore.setState({ connectionStatus: { agents: 'closed' } })
    render(<AgentStatusIndicator workspaceId="ws1" />)
    const dot = screen.getByTestId('agents-connection-status')
    expect(dot).toBeInTheDocument()
    expect(dot).toHaveAttribute('title', 'Agent 连接已断开')
  })

  it('shows a connecting status dot when the agents channel is connecting', () => {
    useExecutorsStore.setState({ connectionStatus: { agents: 'connecting' } })
    render(<AgentStatusIndicator workspaceId="ws1" />)
    const dot = screen.getByTestId('agents-connection-status')
    expect(dot).toBeInTheDocument()
    expect(dot).toHaveAttribute('title', 'Agent 连接中')
  })

  it('hides the status dot when the agents channel is open', () => {
    useExecutorsStore.setState({ connectionStatus: { agents: 'open' } })
    render(<AgentStatusIndicator workspaceId="ws1" />)
    expect(
      screen.queryByTestId('agents-connection-status')
    ).not.toBeInTheDocument()
  })

  it('refreshes registered workers on mount', () => {
    render(<AgentStatusIndicator workspaceId="ws1" />)
    expect(refreshWorkersMock).toHaveBeenCalled()
  })

  it('shows online and offline chips with last-seen heartbeat', () => {
    useExecutorsStore.setState({
      workers: [
        makeWorker({ worker_id: 'w-online', name: 'Online Mac', online: true }),
        makeWorker({
          worker_id: 'w-offline',
          name: 'Offline Mac',
          online: false,
          last_seen_at: '2026-07-22 01:00:00',
        }),
      ],
    })
    render(<AgentStatusIndicator workspaceId="ws1" />)
    expect(screen.getByText('已注册 Worker')).toBeInTheDocument()
    expect(screen.getByText('Online Mac')).toBeInTheDocument()
    expect(screen.getByText('Offline Mac')).toBeInTheDocument()
    expect(screen.getByTitle('最近心跳 2026-07-22 02:15:31')).toHaveTextContent(
      '在线'
    )
    expect(screen.getByTitle('最近心跳 2026-07-22 01:00:00')).toHaveTextContent(
      '离线'
    )
  })

  it('filters workers by allowed workspaces; empty list means all workspaces', () => {
    useExecutorsStore.setState({
      workers: [
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
      ],
    })
    render(<AgentStatusIndicator workspaceId="ws1" />)
    expect(screen.getByText('Global Mac')).toBeInTheDocument()
    expect(screen.getByText('Scoped Mac')).toBeInTheDocument()
    expect(screen.queryByText('Other Mac')).not.toBeInTheDocument()
  })

  it('does not show revoked workers', () => {
    useExecutorsStore.setState({
      workers: [
        makeWorker({
          worker_id: 'w-revoked',
          name: 'Revoked Mac',
          revoked: true,
        }),
      ],
    })
    render(<AgentStatusIndicator workspaceId="ws1" />)
    expect(screen.queryByText('Revoked Mac')).not.toBeInTheDocument()
    // Local agent rows without a registered Worker still render.
    expect(screen.getByText('Main')).toBeInTheDocument()
  })

  it('merges registered worker info and workspace workload into one row', () => {
    useExecutorsStore.setState({
      workers: [
        makeWorker({
          worker_id: 'mac-air',
          name: 'MacbookAir',
          online: true,
          max_concurrency: 30,
        }),
      ],
    })
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
    render(<AgentStatusIndicator workspaceId="ws1" />)
    expect(screen.getAllByText('MacbookAir')).toHaveLength(1)
    expect(screen.getByText('忙碌 3/30')).toBeInTheDocument()
    expect(screen.getByText('在线')).toBeInTheDocument()
  })

  it('falls back to worker capacity when no workload row exists yet', () => {
    useExecutorsStore.setState({
      workers: [
        makeWorker({
          worker_id: 'w-idle',
          name: 'Idle Mac',
          online: true,
          max_concurrency: 10,
        }),
      ],
    })
    mockAgents = []
    render(<AgentStatusIndicator workspaceId="ws1" />)
    expect(screen.getByText('Idle Mac')).toBeInTheDocument()
    expect(screen.getByText('空闲 0/10')).toBeInTheDocument()
  })
})
