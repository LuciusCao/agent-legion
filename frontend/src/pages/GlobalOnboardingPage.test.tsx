import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { Routes, Route, useLocation } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import GlobalOnboardingPage from './GlobalOnboardingPage'
import { getStudioAgents } from '../api/studioAgents'
import type { StudioAgentRegistryResponse } from '../api/studioAgents'
import { useAuthStore } from '../stores/authStore'
import type { UserResponse } from '../api/authApi'

vi.mock('../api/studioAgents', () => ({
  getStudioAgents: vi.fn(),
  updateStudioAgents: vi.fn(),
}))

const DISMISS_KEY = 'agent-legion:global-onboarding-dismissed'

// 该 jsdom 环境不提供 localStorage：用内存 stub 验证持久化读写（同
// useStudioRightPanelWidth.test.tsx 先例）。
function installLocalStorageStub() {
  const store = new Map<string, string>()
  const stub: Storage = {
    get length() {
      return store.size
    },
    clear: () => store.clear(),
    getItem: (key) => store.get(key) ?? null,
    key: (index) => [...store.keys()][index] ?? null,
    removeItem: (key) => void store.delete(key),
    setItem: (key, value) => void store.set(key, String(value)),
  }
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: stub,
  })
  return stub
}

const adminUser: UserResponse = {
  id: 'u1',
  username: 'admin',
  display_name: '管理员',
  role: 'admin',
  disabled_at: null,
  created_at: '2026-01-01T00:00:00Z',
}

// 当前契约：顶层 availability 映射。
const registry: StudioAgentRegistryResponse = {
  api_base: 'http://127.0.0.1:8000',
  agents: [
    {
      id: 'kimi',
      label: 'Kimi Code',
      command: 'kimi',
      args: ['acp'],
      source: 'manual',
    },
    {
      id: 'claude',
      label: 'Claude Code',
      command: 'claude',
      args: [],
      source: 'detected',
    },
  ],
  availability: { kimi: true, claude: false },
}

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname}</div>
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/admin/onboarding']}>
      <Routes>
        <Route path="/admin/onboarding" element={<GlobalOnboardingPage />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>
  )
}

let storageStub: Storage

beforeEach(() => {
  vi.clearAllMocks()
  storageStub = installLocalStorageStub()
  useAuthStore.setState({
    user: adminUser,
    status: 'authenticated',
    bootstrapAvailable: false,
  })
  vi.mocked(getStudioAgents).mockResolvedValue(registry)
})

describe('GlobalOnboardingPage', () => {
  it('lists ACP agents with availability from the registry', async () => {
    renderPage()

    expect(
      await screen.findByRole('heading', { name: '全局初始化清单' })
    ).toBeInTheDocument()
    expect(await screen.findByText('Kimi Code')).toBeInTheDocument()
    expect(screen.getByText('kimi acp')).toBeInTheDocument()
    expect(screen.getByText('可用')).toBeInTheDocument()
    expect(screen.getByText('不可用')).toBeInTheDocument()
    // 有不可用项 → 清单项待确认。
    expect(screen.getByText('待确认')).toBeInTheDocument()
  })

  it('marks the item ready when every agent is available', async () => {
    vi.mocked(getStudioAgents).mockResolvedValue({
      ...registry,
      availability: { kimi: true, claude: true },
    })

    renderPage()

    expect(await screen.findByText('已就绪')).toBeInTheDocument()
  })

  it('accepts the #332 per-agent detection shape (availability/detected/source)', async () => {
    // M2 契约：逐项 availability/detected + source，无顶层映射。
    vi.mocked(getStudioAgents).mockResolvedValue({
      api_base: 'http://127.0.0.1:8000',
      agents: [
        {
          id: 'kimi',
          label: 'Kimi Code',
          command: 'kimi',
          args: ['acp'],
          source: 'detected',
          availability: true,
        },
        {
          id: 'claude',
          label: 'Claude Code',
          command: 'claude',
          args: [],
          source: 'registry',
          detected: false,
        },
      ],
    } as unknown as StudioAgentRegistryResponse)

    renderPage()

    expect(await screen.findByText('Kimi Code')).toBeInTheDocument()
    expect(screen.getByText('detected')).toBeInTheDocument()
    expect(screen.getByText('registry')).toBeInTheDocument()
    expect(screen.getByText('可用')).toBeInTheDocument()
    expect(screen.getByText('不可用')).toBeInTheDocument()
  })

  it('shows an empty-state hint when no agent is registered', async () => {
    vi.mocked(getStudioAgents).mockResolvedValue({
      api_base: 'http://127.0.0.1:8000',
      agents: [],
      availability: {},
    })

    renderPage()

    expect(
      await screen.findByText(/尚未探测到可用的 ACP agent/)
    ).toBeInTheDocument()
  })

  it('dismisses and navigates home via 进入产品', async () => {
    renderPage()
    await screen.findByText('Kimi Code')

    fireEvent.click(screen.getByRole('button', { name: '进入产品' }))

    expect(storageStub.getItem(DISMISS_KEY)).toBe('1')
    expect((await screen.findByTestId('location')).textContent).toBe('/')
  })

  it('dismisses and navigates to global settings via 去全局设置', async () => {
    renderPage()
    await screen.findByText('Kimi Code')

    fireEvent.click(screen.getByRole('button', { name: '去全局设置' }))

    expect(storageStub.getItem(DISMISS_KEY)).toBe('1')
    expect((await screen.findByTestId('location')).textContent).toBe(
      '/admin/settings'
    )
  })

  it('notes the revisit when the checklist was already dismissed', async () => {
    storageStub.setItem(DISMISS_KEY, '1')

    renderPage()

    expect(
      await screen.findByText(/之前已完成或跳过该清单/)
    ).toBeInTheDocument()
  })

  it('redirects non-admin users home', async () => {
    useAuthStore.setState({
      user: { ...adminUser, role: 'member' },
    })

    renderPage()

    expect((await screen.findByTestId('location')).textContent).toBe('/')
    expect(getStudioAgents).not.toHaveBeenCalled()
  })
})
