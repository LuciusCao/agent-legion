import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { AgentAllocationList } from './AgentAllocationList'
import { useUiStore } from '../stores/uiStore'
import {
  assignAgent,
  fetchAgents,
  fetchWorkspaceAgents,
  unassignAgent,
} from '../api'

vi.mock('../api', () => ({
  api: vi.fn(),
  fetchAgents: vi.fn(),
  fetchWorkspaceAgents: vi.fn(),
  assignAgent: vi.fn(),
  unassignAgent: vi.fn(),
}))

const mockFetchAgents = vi.mocked(fetchAgents)
const mockFetchWorkspaceAgents = vi.mocked(fetchWorkspaceAgents)
const mockAssignAgent = vi.mocked(assignAgent)
const mockUnassignAgent = vi.mocked(unassignAgent)

const agents = [
  {
    id: 'a1',
    name: 'Agent One',
    busy: false,
    task_count: 0,
    max_tasks: 2,
    current_video_id: null,
  },
  {
    id: 'a2',
    name: 'Agent Two',
    busy: true,
    task_count: 1,
    max_tasks: 1,
    current_video_id: 'v1',
  },
]

describe('AgentAllocationList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useUiStore.setState({ toast: null })
    mockFetchAgents.mockReset()
    mockFetchAgents.mockResolvedValue({ agents: [] })
    mockFetchWorkspaceAgents.mockReset()
    mockFetchWorkspaceAgents.mockResolvedValue({ agents: [] })
    mockAssignAgent.mockReset()
    mockUnassignAgent.mockReset()
  })

  it('shows loading state initially', () => {
    mockFetchAgents.mockImplementation(() => new Promise(() => {}))
    mockFetchWorkspaceAgents.mockImplementation(() => new Promise(() => {}))
    render(<AgentAllocationList workspaceId="ws1" />)
    expect(screen.getByText('加载中...')).toBeInTheDocument()
  })

  it('renders available and assigned agents after fetch', async () => {
    mockFetchAgents.mockResolvedValue({ agents })
    mockFetchWorkspaceAgents.mockResolvedValue({
      agents: [{ agent_id: 'a1', concurrency_limit: 2 }],
    })
    render(<AgentAllocationList workspaceId="ws1" />)

    await waitFor(() => {
      expect(screen.getByText('Agent One')).toBeInTheDocument()
    })

    expect(
      within(screen.getByTestId('assigned-agents')).getByText('Agent One')
    ).toBeInTheDocument()
    expect(
      within(screen.getByTestId('available-agents')).getByText('Agent Two')
    ).toBeInTheDocument()
  })

  it('opens concurrency input and assigns agent on confirm', async () => {
    let workspaceAgents: { agent_id: string; concurrency_limit: number }[] = []
    mockFetchAgents.mockResolvedValue({ agents })
    mockFetchWorkspaceAgents.mockImplementation(() =>
      Promise.resolve({ agents: workspaceAgents })
    )
    mockAssignAgent.mockResolvedValue({
      agent_id: 'a2',
      workspace_id: 'ws1',
      concurrency_limit: 3,
    })

    render(<AgentAllocationList workspaceId="ws1" />)
    await waitFor(() => {
      expect(screen.getByText('Agent Two')).toBeInTheDocument()
    })

    const agentTwoRow = screen
      .getByText('Agent Two')
      .closest('li') as HTMLElement
    const assignBtn = within(agentTwoRow).getByText('分配')
    await act(async () => {
      assignBtn.click()
    })

    const input = agentTwoRow.querySelector(
      'md-outlined-text-field[aria-label="并发限制"]'
    ) as HTMLInputElement
    expect(input).toBeInTheDocument()
    input.value = '3'
    fireEvent.input(input)

    workspaceAgents = [{ agent_id: 'a2', concurrency_limit: 3 }]
    await act(async () => {
      screen.getByText('确认').click()
    })

    await waitFor(() => {
      expect(mockAssignAgent).toHaveBeenCalledWith('ws1', 'a2', 3)
    })
    expect(mockFetchWorkspaceAgents).toHaveBeenCalledTimes(2)
    expect(useUiStore.getState().toast).toEqual({
      message: '分配成功',
      type: 'success',
    })
  })

  it('unassigns agent when cancel button is clicked', async () => {
    let workspaceAgents: { agent_id: string; concurrency_limit: number }[] = [
      { agent_id: 'a1', concurrency_limit: 2 },
    ]
    mockFetchAgents.mockResolvedValue({ agents })
    mockFetchWorkspaceAgents.mockImplementation(() =>
      Promise.resolve({ agents: workspaceAgents })
    )
    mockUnassignAgent.mockResolvedValue({
      agent_id: 'a1',
      workspace_id: 'ws1',
      removed: true,
    })

    render(<AgentAllocationList workspaceId="ws1" />)
    await waitFor(() => {
      expect(screen.getByText('Agent One')).toBeInTheDocument()
    })

    const agentOneRow = screen
      .getByText('Agent One')
      .closest('li') as HTMLElement
    const unassignBtn = within(agentOneRow).getByText('取消分配')
    workspaceAgents = []
    await act(async () => {
      unassignBtn.click()
    })

    await waitFor(() => {
      expect(mockUnassignAgent).toHaveBeenCalledWith('ws1', 'a1')
    })
    expect(mockFetchWorkspaceAgents).toHaveBeenCalledTimes(2)
    expect(useUiStore.getState().toast).toEqual({
      message: '已取消分配',
      type: 'success',
    })
  })

  it('updates concurrency limit and saves assignment', async () => {
    let workspaceAgents: { agent_id: string; concurrency_limit: number }[] = [
      { agent_id: 'a1', concurrency_limit: 2 },
    ]
    mockFetchAgents.mockResolvedValue({ agents })
    mockFetchWorkspaceAgents.mockImplementation(() =>
      Promise.resolve({ agents: workspaceAgents })
    )
    mockAssignAgent.mockResolvedValue({
      agent_id: 'a1',
      workspace_id: 'ws1',
      concurrency_limit: 5,
    })

    render(<AgentAllocationList workspaceId="ws1" />)
    await waitFor(() => {
      expect(screen.getByText('Agent One')).toBeInTheDocument()
    })

    const agentOneRow = screen
      .getByText('Agent One')
      .closest('li') as HTMLElement
    const input = agentOneRow.querySelector(
      'md-outlined-text-field[aria-label="并发限制"]'
    ) as HTMLInputElement
    input.value = '5'
    fireEvent.input(input)

    workspaceAgents = [{ agent_id: 'a1', concurrency_limit: 5 }]
    await act(async () => {
      within(agentOneRow).getByText('保存分配').click()
    })

    await waitFor(() => {
      expect(mockAssignAgent).toHaveBeenCalledWith('ws1', 'a1', 5)
    })
    expect(mockFetchWorkspaceAgents).toHaveBeenCalledTimes(2)
    expect(useUiStore.getState().toast).toEqual({
      message: '并发限制已更新',
      type: 'success',
    })
  })

  it('shows toast on API error', async () => {
    mockFetchAgents.mockResolvedValue({ agents })
    mockFetchWorkspaceAgents.mockResolvedValue({ agents: [] })
    mockAssignAgent.mockRejectedValue(new Error('network error'))

    render(<AgentAllocationList workspaceId="ws1" />)
    await waitFor(() => {
      expect(screen.getByText('Agent Two')).toBeInTheDocument()
    })

    const agentTwoRow = screen
      .getByText('Agent Two')
      .closest('li') as HTMLElement
    await act(async () => {
      within(agentTwoRow).getByText('分配').click()
    })

    const input = agentTwoRow.querySelector(
      'md-outlined-text-field[aria-label="并发限制"]'
    ) as HTMLInputElement
    input.value = '2'
    fireEvent.input(input)

    await act(async () => {
      screen.getByText('确认').click()
    })

    await waitFor(() => {
      expect(useUiStore.getState().toast).toEqual({
        message: 'network error',
        type: 'error',
      })
    })
  })
})
