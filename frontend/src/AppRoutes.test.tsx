import { Suspense } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from './testing/TestMemoryRouter'
import AppRoutes from './AppRoutes'
import type { UserResponse } from './api/authApi'

const initialize = vi.fn()

const authState: {
  user: UserResponse | null
  status: 'unknown' | 'authenticated' | 'anonymous'
  bootstrapAvailable: boolean | null
  initialize: typeof initialize
  login: ReturnType<typeof vi.fn>
  logout: ReturnType<typeof vi.fn>
  bootstrap: ReturnType<typeof vi.fn>
} = {
  user: null,
  status: 'anonymous',
  bootstrapAvailable: false,
  initialize,
  login: vi.fn(),
  logout: vi.fn(),
  bootstrap: vi.fn(),
}

vi.mock('./stores/authStore', () => ({
  useAuthStore: (selector?: (s: typeof authState) => unknown) =>
    selector ? selector(authState) : authState,
}))

// Replace lazy page chunks with instant stubs: guard behavior must not
// depend on dynamic-import timing under load.
vi.mock('./routes/pages', () => {
  const stub = (label: string) => () => <div>{label}</div>
  return {
    LoginPage: stub('登录 Agent Legion'),
    SetupPage: stub('初始化管理员'),
    UsersAdminPage: stub('用户管理'),
    JobDetailPage: stub('job-detail'),
    DashboardPage: stub('dashboard'),
    WorkspaceLayout: stub('workspace-layout'),
    SettingsPage: stub('settings'),
    WorkflowStudioPage: stub('workflow-studio'),
    WorkspaceMainPage: stub('workspace-main'),
    TokenUsagePage: stub('token-usage'),
    MonitoringPage: stub('monitoring'),
    QualityPage: stub('quality'),
  }
})

function renderRoutes(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Suspense fallback={null}>
        <AppRoutes />
      </Suspense>
    </MemoryRouter>
  )
}

beforeEach(() => {
  authState.user = null
  authState.status = 'anonymous'
  authState.bootstrapAvailable = false
  initialize.mockClear()
})

describe('AppRoutes auth guard', () => {
  it('shows a loading state and initializes while status is unknown', () => {
    authState.status = 'unknown'

    renderRoutes('/')

    expect(screen.getByText('加载中…')).toBeInTheDocument()
    expect(initialize).toHaveBeenCalled()
  })

  it('redirects anonymous users to /login', async () => {
    renderRoutes('/')

    expect(
      await screen.findByText('登录 Agent Legion', undefined, { timeout: 5000 })
    ).toBeInTheDocument()
  })

  it('redirects to /setup when bootstrap is available', async () => {
    authState.bootstrapAvailable = true

    renderRoutes('/')

    expect(
      await screen.findByText('初始化管理员', undefined, { timeout: 5000 })
    ).toBeInTheDocument()
  })
})
