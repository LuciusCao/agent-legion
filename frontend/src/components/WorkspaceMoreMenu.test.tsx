import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Routes, Route } from 'react-router-dom'
import { WorkspaceMoreMenu } from './WorkspaceMoreMenu'
import { MemoryRouter } from '../testing/TestMemoryRouter'
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

const setWorkspacePackageDialogOpenMock = vi.fn()
vi.mock('../stores/uiStore', () => ({
  useUiStore: (
    selector?: (state: ReturnType<typeof createMockUiState>) => unknown
  ) => {
    const state = createMockUiState({
      setWorkspacePackageDialogOpen: setWorkspacePackageDialogOpenMock,
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

function renderMenu() {
  return render(
    <MemoryRouter initialEntries={['/workspaces/ws1']}>
      <Routes>
        <Route
          path="/workspaces/:workspaceId/*"
          element={<WorkspaceMoreMenu />}
        />
      </Routes>
    </MemoryRouter>
  )
}

function openMenu() {
  fireEvent.click(screen.getByLabelText('更多操作'))
}

describe('WorkspaceMoreMenu', () => {
  beforeEach(() => {
    mockNavigate.mockClear()
    setWorkspacePackageDialogOpenMock.mockClear()
    authState.user = { role: 'admin' }
  })

  it('renders the hover panel as a menu', () => {
    renderMenu()
    expect(screen.getByRole('menu')).toBeInTheDocument()
  })

  it('lists package, token usage, quality, studio and settings entries', () => {
    renderMenu()
    openMenu()
    expect(screen.getByLabelText('包历史')).toBeInTheDocument()
    expect(screen.getByLabelText('Token 使用分析')).toBeInTheDocument()
    expect(screen.getByLabelText('质量闭环')).toBeInTheDocument()
    expect(screen.getByLabelText('Workflow Studio')).toBeInTheDocument()
    expect(screen.getByLabelText('设置')).toBeInTheDocument()
  })

  it('opens the package history dialog without navigating', () => {
    renderMenu()
    openMenu()
    fireEvent.click(screen.getByLabelText('包历史'))
    expect(setWorkspacePackageDialogOpenMock).toHaveBeenCalledWith(true)
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('navigates to the quality page', () => {
    renderMenu()
    openMenu()
    fireEvent.click(screen.getByLabelText('质量闭环'))
    expect(mockNavigate).toHaveBeenCalledWith('/workspaces/ws1/quality')
  })

  it('hides the studio entry for non-admin users', () => {
    authState.user = { role: 'member' }
    renderMenu()
    openMenu()
    expect(screen.queryByLabelText('Workflow Studio')).not.toBeInTheDocument()
    expect(screen.getByLabelText('设置')).toBeInTheDocument()
  })
})
