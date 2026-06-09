import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import WorkspaceLayout from './WorkspaceLayout'

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual =
    await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

vi.mock('../views/WorkspaceOverview', () => ({
  default: () => <div data-testid="overview">Workspace Overview</div>,
}))
vi.mock('../views/WorkspaceJobList', () => ({
  default: () => <div data-testid="job-list">JobList</div>,
}))
vi.mock('../views/WorkspaceJobDetail', () => ({
  default: () => <div data-testid="job-detail">JobDetail</div>,
}))

vi.mock('../stores/workspaceStore', () => ({
  useWorkspaceStore: () => ({
    workspaces: [
      {
        id: 'ws1',
        name: '测试空间',
        default_pipeline_key: 'question_content',
        default_entity: 'question',
      },
    ],
    currentWorkspace: {
      id: 'ws1',
      name: '测试空间',
      default_pipeline_key: 'question_content',
      default_entity: 'question',
    },
    workspaceStats: {
      ws1: { pipeline_key: 'question_content', job_stats: {} },
    },
    fetchWorkspaces: vi.fn(),
    setCurrentWorkspace: vi.fn(),
    fetchWorkspaceStats: vi.fn(),
  }),
}))

const setWorkerPausedMock = vi.fn()
const fetchWorkerStatusMock = vi.fn()

vi.mock('../stores/uiStore', () => ({
  useUiStore: () => ({
    workerPaused: false,
    fetchWorkerStatus: fetchWorkerStatusMock,
    setWorkerPaused: setWorkerPausedMock,
  }),
}))

describe('WorkspaceLayout', () => {
  beforeEach(() => {
    mockNavigate.mockClear()
    setWorkerPausedMock.mockClear()
    fetchWorkerStatusMock.mockClear()
  })

  it('renders app bar with workspace name and pipeline tag', () => {
    render(
      <MemoryRouter initialEntries={['/workspaces/ws1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/*"
            element={<WorkspaceLayout />}
          />
        </Routes>
      </MemoryRouter>
    )
    expect(screen.getByText('测试空间')).toBeInTheDocument()
    expect(screen.getByText('question_content')).toBeInTheDocument()
  })

  it('renders settings button', () => {
    render(
      <MemoryRouter initialEntries={['/workspaces/ws1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/*"
            element={<WorkspaceLayout />}
          />
        </Routes>
      </MemoryRouter>
    )
    expect(screen.getByText('设置')).toBeInTheDocument()
  })

  it('does not render sidebar tabs', () => {
    render(
      <MemoryRouter initialEntries={['/workspaces/ws1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/*"
            element={<WorkspaceLayout />}
          />
        </Routes>
      </MemoryRouter>
    )
    expect(screen.queryByText('Overview')).not.toBeInTheDocument()
    expect(screen.queryByText('Jobs')).not.toBeInTheDocument()
  })

  it('navigates to settings when settings button is clicked', () => {
    render(
      <MemoryRouter initialEntries={['/workspaces/ws1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/*"
            element={<WorkspaceLayout />}
          />
        </Routes>
      </MemoryRouter>
    )
    fireEvent.click(screen.getByText('设置'))
    expect(mockNavigate).toHaveBeenCalledWith('/workspaces/ws1/settings')
  })

  it('navigates to home when back button is clicked', () => {
    render(
      <MemoryRouter initialEntries={['/workspaces/ws1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/*"
            element={<WorkspaceLayout />}
          />
        </Routes>
      </MemoryRouter>
    )
    fireEvent.click(screen.getByText('arrow_back'))
    expect(mockNavigate).toHaveBeenCalledWith('/')
  })
})
