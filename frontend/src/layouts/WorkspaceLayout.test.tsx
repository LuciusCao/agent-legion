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

function renderLayout(initialEntry = '/workspaces/ws1') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route
          path="/workspaces/:workspaceId/*"
          element={<WorkspaceLayout />}
        />
      </Routes>
    </MemoryRouter>
  )
}

function openMoreMenu() {
  fireEvent.click(screen.getByLabelText('更多操作'))
}

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
    renderLayout()
    expect(await screen.findByText('测试空间')).toBeInTheDocument()
    expect(screen.queryByText('question_content')).not.toBeInTheDocument()
  })

  it('keeps low-frequency entries inside the more menu', () => {
    renderLayout()
    // 设置不再作为顶栏按钮存在，只在「更多」面板的菜单项里。
    expect(
      screen.queryByRole('button', { name: '设置' })
    ).not.toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: '设置' })).toBeInTheDocument()
  })

  it('does not render sidebar tabs', () => {
    renderLayout()
    expect(screen.queryByText('Overview')).not.toBeInTheDocument()
    expect(screen.queryByText('Jobs')).not.toBeInTheDocument()
  })

  it('navigates to settings from the more menu', () => {
    renderLayout()
    openMoreMenu()
    fireEvent.click(screen.getByLabelText('设置'))
    expect(mockNavigate).toHaveBeenCalledWith('/workspaces/ws1/settings')
  })

  it('navigates to workflow studio from the more menu', () => {
    renderLayout()
    openMoreMenu()
    fireEvent.click(screen.getByLabelText('Workflow Studio'))
    expect(mockNavigate).toHaveBeenCalledWith('/workspaces/ws1/workflow-studio')
  })

  it('hides the workflow studio entry for non-admin users (P4)', () => {
    authState.user = { role: 'member' }
    renderLayout()
    openMoreMenu()
    expect(screen.queryByLabelText('Workflow Studio')).not.toBeInTheDocument()
  })

  it('navigates to the monitoring page when the monitoring button is clicked', () => {
    renderLayout()
    fireEvent.click(screen.getByLabelText('运维监控'))
    expect(mockNavigate).toHaveBeenCalledWith('/workspaces/ws1/monitoring')
  })

  it('navigates to home when home button is clicked', () => {
    renderLayout()
    fireEvent.click(screen.getByTestId('app-bar-home'))
    expect(mockNavigate).toHaveBeenCalledWith('/')
  })

  it('has transparent border when not scrolled', () => {
    const { container } = renderLayout()
    const header = container.querySelector('[data-testid="app-bar"]')
    expect(header).toBeTruthy()
    expect(header!.classList.contains(appBarStyles.scrolled)).toBe(false)
  })

  it('applies elevation shadow when main content is scrolled', () => {
    const { container } = renderLayout()
    const main = container.querySelector('main')
    expect(main).toBeTruthy()
    act(() => {
      main!.scrollTop = 10
      main!.dispatchEvent(new Event('scroll', { bubbles: false }))
    })
    const header = container.querySelector('[data-testid="app-bar"]')
    expect(header!.classList.contains(appBarStyles.scrolled)).toBe(true)
  })

  it('renders the run/pause control in the app bar', () => {
    renderLayout()
    expect(screen.getByLabelText('暂停运行')).toBeInTheDocument()
  })

  it('opens workspace package history dialog from the more menu', () => {
    renderLayout()
    openMoreMenu()
    fireEvent.click(screen.getByLabelText('包历史'))
    expect(setWorkspacePackageDialogOpenMock).toHaveBeenCalledWith(true)
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('navigates to token usage page from the more menu', () => {
    renderLayout()
    openMoreMenu()
    fireEvent.click(screen.getByLabelText('Token 使用分析'))
    expect(mockNavigate).toHaveBeenCalledWith('/workspaces/ws1/token-usage')
    expect(setTokenUsageDialogOpenMock).not.toHaveBeenCalled()
  })

  it('renders token analysis button on the job detail page', () => {
    renderLayout('/workspaces/ws1/jobs/j1')
    expect(screen.getByLabelText('Token 使用分析')).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('Token 使用分析'))
    expect(setTokenUsageDialogOpenMock).toHaveBeenCalledWith(true)
    expect(mockNavigate).not.toHaveBeenCalled()
  })
})
