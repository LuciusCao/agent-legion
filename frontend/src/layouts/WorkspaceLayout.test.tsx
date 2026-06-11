import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import WorkspaceLayout from './WorkspaceLayout'
import appBarStyles from '../components/AppBar.module.css'
import { createMockUiState } from '../testing/fixtures'

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
  useUiStore: (
    selector?: (state: ReturnType<typeof createMockUiState>) => unknown
  ) => {
    const state = createMockUiState({
      fetchWorkerStatus: fetchWorkerStatusMock,
      setWorkerPaused: setWorkerPausedMock,
    })
    return selector ? selector(state) : state
  },
}))

describe('WorkspaceLayout', () => {
  beforeEach(() => {
    mockNavigate.mockClear()
    setWorkerPausedMock.mockClear()
    fetchWorkerStatusMock.mockClear()
  })

  it('renders app bar with workspace name and no pipeline tag', () => {
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
    expect(screen.queryByText('question_content')).not.toBeInTheDocument()
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
    expect(screen.getByLabelText('设置')).toBeInTheDocument()
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
    fireEvent.click(screen.getByLabelText('设置'))
    expect(mockNavigate).toHaveBeenCalledWith('/workspaces/ws1/settings')
  })

  it('navigates to home when home button is clicked', () => {
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
    fireEvent.click(screen.getByTestId('app-bar-home'))
    expect(mockNavigate).toHaveBeenCalledWith('/')
  })

  it('has transparent border when not scrolled', () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/workspaces/ws1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/*"
            element={<WorkspaceLayout />}
          />
        </Routes>
      </MemoryRouter>
    )
    const header = container.querySelector('[data-testid="app-bar"]')
    expect(header).toBeTruthy()
    expect(header!.classList.contains(appBarStyles.scrolled)).toBe(false)
  })

  it('applies elevation shadow when main content is scrolled', () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/workspaces/ws1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/*"
            element={<WorkspaceLayout />}
          />
        </Routes>
      </MemoryRouter>
    )
    const main = container.querySelector('main')
    expect(main).toBeTruthy()
    act(() => {
      main!.scrollTop = 10
      main!.dispatchEvent(new Event('scroll', { bubbles: false }))
    })
    const header = container.querySelector('[data-testid="app-bar"]')
    expect(header!.classList.contains(appBarStyles.scrolled)).toBe(true)
  })
})
