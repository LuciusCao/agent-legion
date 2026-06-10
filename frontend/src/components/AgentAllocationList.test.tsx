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
import { fetchAgents } from '../api'

vi.mock('../api', () => ({
  api: vi.fn(),
  fetchAgents: vi.fn(),
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
        onAssignmentsChange={() => {}}
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
        onAssignmentsChange={() => {}}
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
    mockFetchAgents.mockResolvedValue({ agents })

    const onAssignmentsChange = vi.fn()
    render(
      <AgentAllocationList
        workspaceId="ws1"
        assignments={null}
        onAssignmentsChange={onAssignmentsChange}
      />
    )
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

    await act(async () => {
      screen.getByText('确认').click()
    })

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
        assignments={null}
        onAssignmentsChange={() => {}}
      />
    )
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

    expect(
      agentTwoRow.querySelector('md-outlined-text-field[aria-label="并发限制"]')
    ).toBeInTheDocument()

    await act(async () => {
      screen.getByText('取消').click()
    })

    expect(
      agentTwoRow.querySelector('md-outlined-text-field[aria-label="并发限制"]')
    ).not.toBeInTheDocument()
    expect(within(agentTwoRow).getByText('分配')).toBeInTheDocument()
  })

  it('assigns agent with concurrency limit on confirm', async () => {
    mockFetchAgents.mockResolvedValue({ agents })

    const onAssignmentsChange = vi.fn()
    render(
      <AgentAllocationList
        workspaceId="ws1"
        assignments={null}
        onAssignmentsChange={onAssignmentsChange}
      />
    )
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
    input.value = '2'
    fireEvent.input(input)

    await act(async () => {
      screen.getByText('确认').click()
    })

    await waitFor(() => {
      expect(onAssignmentsChange).toHaveBeenCalledWith([
        { agent_id: 'a2', concurrency_limit: 2 },
      ])
    })
  })

  it('shows toast error when initial fetch fails', async () => {
    mockFetchAgents.mockRejectedValue(new Error('fetch agents failed'))

    render(
      <AgentAllocationList
        workspaceId="ws1"
        assignments={null}
        onAssignmentsChange={() => {}}
      />
    )

    await waitFor(() => {
      expect(useUiStore.getState().toast).toEqual({
        message: 'fetch agents failed',
        type: 'error',
      })
    })
  })

  it('unassigns agent when cancel button is clicked', async () => {
    mockFetchAgents.mockResolvedValue({ agents })

    const onAssignmentsChange = vi.fn()
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

    const agentOneRow = screen
      .getByText('Agent One')
      .closest('li') as HTMLElement
    const unassignBtn = within(agentOneRow).getByText('取消分配')
    await act(async () => {
      unassignBtn.click()
    })

    await waitFor(() => {
      expect(onAssignmentsChange).toHaveBeenCalledWith([])
    })
  })

  it('updates concurrency limit and saves assignment', async () => {
    mockFetchAgents.mockResolvedValue({ agents })

    const onAssignmentsChange = vi.fn()
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

    const agentOneRow = screen
      .getByText('Agent One')
      .closest('li') as HTMLElement
    const input = agentOneRow.querySelector(
      'md-outlined-text-field[aria-label="并发限制"]'
    ) as HTMLInputElement
    input.value = '5'
    fireEvent.input(input)

    await waitFor(() => {
      expect(onAssignmentsChange).toHaveBeenCalledWith([
        { agent_id: 'a1', concurrency_limit: 5 },
      ])
    })
  })
})
