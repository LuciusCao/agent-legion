import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import { DashboardPage } from './DashboardPage'
import { EventSourceMock } from '../testing/eventSourceMock'
import { makeWorkspace } from '../testing/workspaceFixtures'
import { fetchWorkspaces, fetchWorkspaceStats, fetchWorkflows } from '../api'
import type { WorkspaceStats } from '../types/workspaceTypes'

vi.mock('../api', () => ({
  fetchWorkspaces: vi.fn(),
  fetchWorkspaceStats: vi.fn(),
  createWorkspace: vi.fn(),
  fetchWorkflows: vi.fn().mockResolvedValue({
    workflows: [{ key: 'demo_workflow', label: '题目审题信息' }],
  }),
}))

const mockFetchWorkspaces = vi.mocked(fetchWorkspaces)
const mockFetchWorkspaceStats = vi.mocked(fetchWorkspaceStats)
const mockFetchWorkflows = vi.mocked(fetchWorkflows)

const navigate = vi.fn()

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return {
    ...actual,
    useNavigate: () => navigate,
  }
})

const authState: { user: { role: 'admin' | 'member' } | null } = {
  user: { role: 'admin' },
}
vi.mock('../stores/authStore', () => ({
  useAuthStore: (selector?: (state: typeof authState) => unknown) =>
    selector ? selector(authState) : authState,
}))

const ws1 = makeWorkspace({
  id: 'ws-1',
  name: 'Test Workspace',
  default_workflow_key: 'demo_workflow',
})

describe('DashboardPage', () => {
  beforeEach(() => {
    mockFetchWorkspaces.mockReset()
    mockFetchWorkspaces.mockResolvedValue({ workspaces: [] })
    mockFetchWorkspaceStats.mockReset()
    mockFetchWorkspaceStats.mockResolvedValue({
      job_stats: {},
    } as WorkspaceStats)
    mockFetchWorkflows.mockClear()
    navigate.mockClear()
    EventSourceMock.reset()
    authState.user = { role: 'admin' }
  })

  it('renders Agent Legion title', () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    )
    expect(screen.getByText('Agent Legion')).toBeInTheDocument()
  })

  it('does not render a hardcoded Video Knowledge card when workspaces are empty', async () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(mockFetchWorkspaces).toHaveBeenCalled()
    })
    expect(screen.queryByText('Video Knowledge')).not.toBeInTheDocument()
  })

  it('opens create workspace dialog', async () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    )
    await act(async () => {
      fireEvent.click(screen.getByText('新建 Workspace'))
    })
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('hides the create workspace button for non-admin users (P4)', () => {
    authState.user = { role: 'member' }
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    )
    expect(screen.queryByText('新建 Workspace')).not.toBeInTheDocument()
  })

  it('fetches workspaces on mount', async () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(mockFetchWorkspaces).toHaveBeenCalled()
    })
  })

  it('renders workspace cards and fetches stats', async () => {
    mockFetchWorkspaces.mockResolvedValue({ workspaces: [ws1] })

    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    )

    expect(await screen.findByText('Test Workspace')).toBeInTheDocument()
    await waitFor(() => {
      expect(mockFetchWorkspaceStats).toHaveBeenCalledWith('ws-1')
    })
  })

  it('shows workflow label from stats when available', async () => {
    mockFetchWorkspaces.mockResolvedValue({ workspaces: [ws1] })
    mockFetchWorkspaceStats.mockResolvedValue({
      workflow_label: '题目理解',
      job_stats: { running: 1, completed: 2, failed: 0 },
      code_pool: { capacity: 16, running: 0, available: 16 },
    } as unknown as WorkspaceStats)

    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    )

    expect(await screen.findByText('题目理解')).toBeInTheDocument()
  })

  it('navigates to video knowledge workspace when it is returned by the API', async () => {
    mockFetchWorkspaces.mockResolvedValue({
      workspaces: [
        makeWorkspace({
          id: 'demo_video_workflow',
          name: 'Video Knowledge',
          default_workflow_key: 'demo_video_workflow',
        }),
      ],
    })

    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    )
    fireEvent.click(await screen.findByText('Video Knowledge'))
    expect(navigate).toHaveBeenCalledWith('/workspaces/demo_video_workflow')
  })

  it('navigates to workspace when clicking workspace card', async () => {
    mockFetchWorkspaces.mockResolvedValue({ workspaces: [ws1] })

    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    )
    fireEvent.click(await screen.findByText('Test Workspace'))
    expect(navigate).toHaveBeenCalledWith('/workspaces/ws-1')
  })

  it('opens one dashboard event stream instead of one per workspace', async () => {
    mockFetchWorkspaces.mockResolvedValue({
      workspaces: [
        makeWorkspace({ id: 'ws1', name: 'One', default_workflow_key: 'wf' }),
        makeWorkspace({ id: 'ws2', name: 'Two', default_workflow_key: 'wf' }),
      ],
    })

    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(EventSourceMock.instances.length).toBe(1)
    })
    expect(EventSourceMock.instances[0].url).toBe('/api/dashboard/events')
  })
})
