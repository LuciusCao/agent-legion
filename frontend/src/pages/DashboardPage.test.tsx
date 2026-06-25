import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import { DashboardPage } from './DashboardPage'

const fetchWorkspaces = vi.fn()
const fetchWorkspaceStats = vi.fn()
const fetchVideos = vi.fn()
const createWorkspace = vi.fn()
const deleteWorkspace = vi.fn()
const navigate = vi.fn()

const mockWorkspaceStore = {
  workspaces: [] as Array<{
    id: string
    name: string
    default_workflow_key: string
  }>,
  workspaceStats: {} as Record<string, unknown>,
  fetchWorkspaces,
  fetchWorkspaceStats,
  createWorkspace,
  deleteWorkspace,
}

const mockVideoStore = {
  videos: [] as Array<unknown>,
  fetchVideos,
}

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return {
    ...actual,
    useNavigate: () => navigate,
  }
})

vi.mock('../stores/workspaceStore', () => ({
  useWorkspaceStore: () => mockWorkspaceStore,
}))

vi.mock('../stores/videoStore', () => ({
  useVideoStore: () => mockVideoStore,
}))

vi.mock('../api', () => ({
  fetchWorkflows: vi.fn().mockResolvedValue({
    workflows: [{ key: 'question_comprehension_info', label: '题目审题信息' }],
  }),
}))

describe('DashboardPage', () => {
  beforeEach(() => {
    mockWorkspaceStore.workspaces = []
    mockWorkspaceStore.workspaceStats = {}
    mockVideoStore.videos = []
    fetchWorkspaces.mockClear()
    fetchWorkspaceStats.mockClear()
    fetchVideos.mockClear()
    createWorkspace.mockClear()
    navigate.mockClear()
  })

  it('renders Agent Legion title', () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    )
    expect(screen.getByText('Agent Legion')).toBeInTheDocument()
  })

  it('renders Video Hive card', () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    )
    expect(screen.getByText('Video Hive')).toBeInTheDocument()
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

  it('fetches workspaces and videos on mount', () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    )
    expect(fetchWorkspaces).toHaveBeenCalled()
    expect(fetchVideos).toHaveBeenCalled()
  })

  it('renders workspace cards and fetches stats', async () => {
    mockWorkspaceStore.workspaces = [
      {
        id: 'ws-1',
        name: 'Test Workspace',
        default_workflow_key: 'question_comprehension_info',
      },
    ]

    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    )

    expect(screen.getByText('Test Workspace')).toBeInTheDocument()
    await waitFor(() => {
      expect(fetchWorkspaceStats).toHaveBeenCalledWith('ws-1')
    })
  })

  it('shows workflow label from stats when available', async () => {
    mockWorkspaceStore.workspaces = [
      {
        id: 'ws-1',
        name: 'Test Workspace',
        default_workflow_key: 'question_comprehension_info',
      },
    ]
    mockWorkspaceStore.workspaceStats = {
      'ws-1': {
        workflow_label: '题目理解',
        job_stats: { running: 1, completed: 2, failed: 0 },
        executor_status: { executors: [] },
      },
    }

    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('题目理解')).toBeInTheDocument()
    })
  })

  it('opens delete dialog for non-default workspace', () => {
    mockWorkspaceStore.workspaces = [
      {
        id: 'ws-1',
        name: 'Deletable Workspace',
        default_workflow_key: 'question_comprehension_info',
      },
    ]

    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    )

    fireEvent.click(screen.getByLabelText('删除'))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('navigates to video-hive when clicking system card', () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    )
    fireEvent.click(screen.getByText('Video Hive'))
    expect(navigate).toHaveBeenCalledWith('/video-hive')
  })

  it('navigates to workspace when clicking workspace card', () => {
    mockWorkspaceStore.workspaces = [
      {
        id: 'ws-1',
        name: 'Test Workspace',
        default_workflow_key: 'question_comprehension_info',
      },
    ]

    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    )
    fireEvent.click(screen.getByText('Test Workspace'))
    expect(navigate).toHaveBeenCalledWith('/workspaces/ws-1')
  })
})
