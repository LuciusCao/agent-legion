import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { AgentAllocationList } from './AgentAllocationList'
import { useUiStore } from '../stores/uiStore'
import { fetchAgents } from '../api'

vi.mock('../api', () => ({
  api: vi.fn(),
  fetchAgents: vi.fn(),
  fetchWorkspaceAgents: vi.fn(),
  assignAgent: vi.fn(),
  unassignAgent: vi.fn(),
}))

const mockFetchAgents = vi.mocked(fetchAgents)

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
  })

  it('shows loading state initially', () => {
    mockFetchAgents.mockImplementation(() => new Promise(() => {}))
    render(
      <AgentAllocationList
        workspaceId="ws1"
        assignments={null}
        onAssignmentsChange={vi.fn()}
      />
    )
    expect(screen.getByText('加载中...')).toBeInTheDocument()
  })

  it('renders available and assigned agents after fetch', async () => {
    mockFetchAgents.mockResolvedValue({ agents })
    render(
      <AgentAllocationList
        workspaceId="ws1"
        assignments={[{ agent_id: 'a1', concurrency_limit: 2 }]}
        onAssignmentsChange={vi.fn()}
      />
    )

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
    const onAssignmentsChange = vi.fn()
    mockFetchAgents.mockResolvedValue({ agents })
    render(
      <AgentAllocationList
        workspaceId="ws1"
        assignments={[]}
        onAssignmentsChange={onAssignmentsChange}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('Agent Two')).toBeInTheDocument()
    })

    fireEvent.click(screen.getAllByText('分配')[1])
    await waitFor(() => {
      expect(screen.getByLabelText('并发限制')).toBeInTheDocument()
    })

    const input = screen.getByLabelText('并发限制') as HTMLInputElement
    input.value = '3'
    fireEvent.input(input)
    fireEvent.click(screen.getByText('确认'))

    await waitFor(() => {
      expect(onAssignmentsChange).toHaveBeenCalledWith([
        { agent_id: 'a2', concurrency_limit: 3 },
      ])
    })
  })

  it('closes inline form when cancel is clicked', async () => {
    mockFetchAgents.mockResolvedValue({ agents })
    render(
      <AgentAllocationList
        workspaceId="ws1"
        assignments={[]}
        onAssignmentsChange={vi.fn()}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('Agent Two')).toBeInTheDocument()
    })

    fireEvent.click(screen.getAllByText('分配')[1])
    await waitFor(() => {
      expect(screen.getByText('取消')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('取消'))
    await waitFor(() => {
      expect(screen.queryByLabelText('并发限制')).not.toBeInTheDocument()
    })
  })

  it('shows empty states when no agents', async () => {
    mockFetchAgents.mockResolvedValue({ agents: [] })
    render(
      <AgentAllocationList
        workspaceId="ws1"
        assignments={[]}
        onAssignmentsChange={vi.fn()}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('暂无可用智能体')).toBeInTheDocument()
    })
    expect(screen.getByText('当前工作空间未分配智能体')).toBeInTheDocument()
  })

  it('shows toast error when initial fetch fails', async () => {
    mockFetchAgents.mockRejectedValue(new Error('network error'))
    render(
      <AgentAllocationList
        workspaceId="ws1"
        assignments={null}
        onAssignmentsChange={vi.fn()}
      />
    )

    await waitFor(() => {
      expect(useUiStore.getState().toast).toEqual({
        message: 'network error',
        type: 'error',
      })
    })
  })

  it('unassigns agent when cancel button is clicked', async () => {
    const onAssignmentsChange = vi.fn()
    mockFetchAgents.mockResolvedValue({ agents })
    render(
      <AgentAllocationList
        workspaceId="ws1"
        assignments={[{ agent_id: 'a1', concurrency_limit: 2 }]}
        onAssignmentsChange={onAssignmentsChange}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('Agent One')).toBeInTheDocument()
    })

    const assignedSection = screen.getByTestId('assigned-agents')
    fireEvent.click(within(assignedSection).getByText('取消分配'))

    await waitFor(() => {
      expect(onAssignmentsChange).toHaveBeenCalledWith([])
    })
  })

  it('updates concurrency limit via inline input', async () => {
    const onAssignmentsChange = vi.fn()
    mockFetchAgents.mockResolvedValue({ agents })
    render(
      <AgentAllocationList
        workspaceId="ws1"
        assignments={[{ agent_id: 'a1', concurrency_limit: 2 }]}
        onAssignmentsChange={onAssignmentsChange}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('Agent One')).toBeInTheDocument()
    })

    const assignedSection = screen.getByTestId('assigned-agents')
    const input = within(assignedSection).getByLabelText(
      '并发限制'
    ) as HTMLInputElement
    input.value = '5'
    fireEvent.input(input)

    await waitFor(() => {
      expect(onAssignmentsChange).toHaveBeenCalledWith([
        { agent_id: 'a1', concurrency_limit: 5 },
      ])
    })
  })

  it('clamps concurrency limit to minimum of 1', async () => {
    const onAssignmentsChange = vi.fn()
    mockFetchAgents.mockResolvedValue({ agents })
    render(
      <AgentAllocationList
        workspaceId="ws1"
        assignments={[{ agent_id: 'a1', concurrency_limit: 2 }]}
        onAssignmentsChange={onAssignmentsChange}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('Agent One')).toBeInTheDocument()
    })

    const assignedSection = screen.getByTestId('assigned-agents')
    const input = within(assignedSection).getByLabelText(
      '并发限制'
    ) as HTMLInputElement
    input.value = '0'
    fireEvent.input(input)

    await waitFor(() => {
      expect(onAssignmentsChange).toHaveBeenCalledWith([
        { agent_id: 'a1', concurrency_limit: 1 },
      ])
    })
  })
})
