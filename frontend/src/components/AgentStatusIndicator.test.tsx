import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { AgentStatusIndicator } from './AgentStatusIndicator'
import { createMockUiState } from '../testing/fixtures'
import { makeAgentStatus } from '../testing/workspaceFixtures'
import type { AgentStatus } from '../types'

const fetchWorkerStatusMock = vi.fn()
const setWorkerPausedMock = vi.fn()
const showToastMock = vi.fn()

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
})
