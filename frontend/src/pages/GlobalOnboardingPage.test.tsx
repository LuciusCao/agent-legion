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
    setItem: (key, value) => store.set(key, String(value)),
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
  return <div data-testid="location">{location.pathname + location.hash}</div>
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
  it('welcomes with the agent-driven pitch and lists detected agents', async () => {
    renderPage()

    expect(
      await screen.findByRole('heading', { name: '连接你的 AI Agent' })
    ).toBeInTheDocument()
    expect(screen.getByText(/和 AI agent 对话来搭建功能/)).toBeInTheDocument()
    expect(await screen.findByText('Kimi Code')).toBeInTheDocument()
    expect(screen.getByText('kimi acp')).toBeInTheDocument()
    expect(screen.getByText('可用')).toBeInTheDocument()
    expect(screen.getByText('不可用')).toBeInTheDocument()
  })

  it('shows the deferred manual-add hint next to the enter button', async () => {
    renderPage()
    await screen.findByText('Kimi Code')

    // 手动添加降级为提示文案（不再跳转），与「进入产品」同在 actions 行。
    expect(
      screen.getByText('你也可以稍后前往设置页面手动添加')
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '手动添加 agent' })
    ).not.toBeInTheDocument()
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

  it('shows an empty-state hint guiding manual add when no agent is detected', async () => {
    vi.mocked(getStudioAgents).mockResolvedValue({
      api_base: 'http://127.0.0.1:8000',
      agents: [],
      availability: {},
    })

    renderPage()

    expect(
      await screen.findByText(/未检测到已安装的 agent/)
    ).toBeInTheDocument()
    // 空状态也保留了 deferred 手动添加提示。
    expect(
      screen.getByText('你也可以稍后前往设置页面手动添加')
    ).toBeInTheDocument()
  })

  it('dismisses and navigates home via 进入产品', async () => {
    renderPage()
    await screen.findByText('Kimi Code')

    fireEvent.click(screen.getByRole('button', { name: '进入产品' }))

    expect(storageStub.getItem(DISMISS_KEY)).toBe('1')
    expect((await screen.findByTestId('location')).textContent).toBe('/')
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
