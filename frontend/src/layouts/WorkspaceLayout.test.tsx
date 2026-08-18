import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { Routes, Route } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import WorkspaceLayout from './WorkspaceLayout'
import appBarStyles from '../components/AppBar.module.css'
import { createMockAgentsState, createMockUiState } from '../testing/fixtures'

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual =
    await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

vi.mock('../views/WorkspaceJobList', () => ({
  default: () => <div data-testid="job-list">JobList</div>,
}))
vi.mock('../views/WorkspaceJobDetail', () => ({
  default: () => <div data-testid="job-detail">JobDetail</div>,
}))

vi.mock('../api', () => ({
  fetchWorkspaces: vi.fn().mockResolvedValue({
    workspaces: [
      {
        id: 'ws1',
        name: '测试空间',
        default_workflow_key: 'question_content',
        default_entity: 'question',
      },
    ],
  }),
}))

const fetchWorkerStatusMock = vi.fn()
const setWorkspacePackageDialogOpenMock = vi.fn()
const setTokenUsageDialogOpenMock = vi.fn()

vi.mock('../stores/agentsStore', () => ({
  useAgentsStore: (
    selector?: (state: ReturnType<typeof createMockAgentsState>) => unknown
  ) => {
    const state = createMockAgentsState({
      fetchWorkerStatus: fetchWorkerStatusMock,
    })
    return selector ? selector(state) : state
  },
}))

vi.mock('../stores/uiStore', () => ({
  useUiStore: (
    selector?: (state: ReturnType<typeof createMockUiState>) => unknown
  ) => {
    const state = createMockUiState({
      setWorkspacePackageDialogOpen: setWorkspacePackageDialogOpenMock,
      setTokenUsageDialogOpen: setTokenUsageDialogOpenMock,
    })
    return selector ? selector(state) : state
  },
}))

const authState: { user: { role: 'admin' | 'member' } | null } = {
  user: { role: 'admin' },
}
vi.mock('../stores/authStore', () => ({
  useAuthStore: (selector?: (state: typeof authState) => unknown) =>
    selector ? selector(authState) : authState,
}))

describe('WorkspaceLayout', () => {
  beforeEach(() => {
    mockNavigate.mockClear()
    fetchWorkerStatusMock.mockClear()
    setWorkspacePackageDialogOpenMock.mockClear()
    setTokenUsageDialogOpenMock.mockClear()
    fetchWorkerStatusMock.mockResolvedValue(undefined)
    authState.user = { role: 'admin' }
  })

  it('renders app bar with workspace name and no workflow tag', async () => {
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
    expect(await screen.findByText('测试空间')).toBeInTheDocument()
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

  it('navigates to workflow studio when workflow studio button is clicked', () => {
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
    fireEvent.click(screen.getByLabelText('Workflow Studio'))
    expect(mockNavigate).toHaveBeenCalledWith('/workspaces/ws1/workflow-studio')
  })

  it('hides the workflow studio button for non-admin users (P4)', () => {
    authState.user = { role: 'member' }
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
    expect(screen.queryByLabelText('Workflow Studio')).not.toBeInTheDocument()
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

  it('renders agent status indicator in the app bar', () => {
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
    expect(screen.getByLabelText('Agent 状态')).toBeInTheDocument()
  })

  it('opens workspace package history dialog when package button is clicked', () => {
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
    fireEvent.click(screen.getByLabelText('包历史'))
    expect(setWorkspacePackageDialogOpenMock).toHaveBeenCalledWith(true)
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('navigates to token usage page when token analysis button is clicked', () => {
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
    fireEvent.click(screen.getByLabelText('Token 使用分析'))
    expect(mockNavigate).toHaveBeenCalledWith('/workspaces/ws1/token-usage')
    expect(setTokenUsageDialogOpenMock).not.toHaveBeenCalled()
  })

  it('renders token analysis button on the job detail page', () => {
    render(
      <MemoryRouter initialEntries={['/workspaces/ws1/jobs/j1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/*"
            element={<WorkspaceLayout />}
          />
        </Routes>
      </MemoryRouter>
    )
    expect(screen.getByLabelText('Token 使用分析')).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('Token 使用分析'))
    expect(setTokenUsageDialogOpenMock).toHaveBeenCalledWith(true)
    expect(mockNavigate).not.toHaveBeenCalled()
  })
})
