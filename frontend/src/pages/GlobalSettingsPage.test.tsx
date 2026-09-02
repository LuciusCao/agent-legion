import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import GlobalSettingsPage from './GlobalSettingsPage'
import { useAuthStore } from '../stores/authStore'
import type { UserResponse } from '../api/authApi'
import {
  getTokenUsagePricing,
  updateTokenUsagePricing,
} from '../api/tokenUsagePricing'
import type { TokenUsagePricingConfigResponse } from '../api/tokenUsagePricing'
import { getInstanceSettings } from '../api/instanceSettings'
import type { InstanceSettingsResponse } from '../api/instanceSettings'

vi.mock('../api/tokenUsagePricing', () => ({
  getTokenUsagePricing: vi.fn(),
  updateTokenUsagePricing: vi.fn(),
}))

vi.mock('../api/instanceSettings', () => ({
  getInstanceSettings: vi.fn(),
  updateInstanceSettings: vi.fn(),
}))

vi.mock('../api/studioAgents', () => ({
  getStudioAgents: vi.fn().mockResolvedValue({
    api_base: 'http://127.0.0.1:8000',
    agents: [],
    availability: {},
  }),
  updateStudioAgents: vi.fn(),
}))

vi.mock('../api/connections', () => ({
  getConnections: vi.fn().mockResolvedValue({ connections: [] }),
  getConnectionTypes: vi.fn().mockResolvedValue({ types: [] }),
  createConnection: vi.fn(),
  updateConnection: vi.fn(),
  deleteConnection: vi.fn(),
  testConnection: vi.fn(),
}))

vi.mock('../api/infraConnections', () => ({
  getInfraConnections: vi.fn().mockResolvedValue({
    database: {
      engine: 'postgresql',
      host: 'db',
      masked_url: 'postgresql://***@db/agent_legion',
      name: 'agent_legion',
      password_set: true,
      port: 5432,
      user: 'legion',
    },
    storage: {
      bucket: 'agent-legion',
      configured: true,
      backend: 'RustFS',
      credentials: 'static',
      endpoint_url: 'http://rustfs:9000',
      public_endpoint_url: 'http://127.0.0.1:9000',
      reachable: true,
      region: 'us-east-1',
    },
  }),
  testInfraConnection: vi.fn(),
}))

const adminUser: UserResponse = {
  id: 'u1',
  username: 'admin',
  display_name: '管理员',
  role: 'admin',
  disabled_at: null,
  created_at: '2026-01-01T00:00:00Z',
}

const memberUser: UserResponse = {
  id: 'u2',
  username: 'alice',
  display_name: 'Alice',
  role: 'member',
  disabled_at: null,
  created_at: '2026-01-02T00:00:00Z',
}

const pricingConfig: TokenUsagePricingConfigResponse = {
  currency: 'CNY',
  pricing: [
    {
      provider: 'gateway',
      model: 'model-a',
      input_per_1m: 3,
      output_per_1m: 15,
      cache_read_per_1m: 0.6,
    },
  ],
}

const instanceSettings: InstanceSettingsResponse = {
  cleanup: {
    log_retention_days: 30,
    run_dir_retention_days: 7,
    interval_seconds: 3600,
  },
  monitoring: { sample_interval_seconds: 15, retention_days: 30 },
  heartbeat_interval_seconds: 10,
  lease_ttl_seconds: 90,
  heartbeat_failure_threshold: 3,
  sweeper_enabled: true,
  sweeper_interval_seconds: 60,
  code_capacity: 16,
  materials_ttl_days: 0,
  execution_retention_days: 0,
  workflows: { enabled: true, max_items_per_run: 20000 },
  agent_workers: { max_archive_bytes: 104857600, min_protocol_version: 2 },
  skills_root: '~/.agents/skills',
}

function renderPage(hash?: string) {
  return render(
    <MemoryRouter
      initialEntries={hash ? [`/admin/settings${hash}`] : undefined}
    >
      <GlobalSettingsPage />
    </MemoryRouter>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getInstanceSettings).mockResolvedValue(instanceSettings)
  useAuthStore.setState({
    user: adminUser,
    status: 'authenticated',
    bootstrapAvailable: false,
  })
})

describe('GlobalSettingsPage', () => {
  it('loads and renders the pricing section', async () => {
    vi.mocked(getTokenUsagePricing).mockResolvedValue(pricingConfig)

    renderPage()

    expect(await screen.findByDisplayValue('gateway')).toBeInTheDocument()
    expect(screen.getByDisplayValue('model-a')).toBeInTheDocument()
    expect(screen.getByDisplayValue('CNY')).toBeInTheDocument()
    expect(
      screen.getByRole('heading', { name: '模型定价' })
    ).toBeInTheDocument()
  })

  it('renders the sidebar nav with entries for each section only', async () => {
    vi.mocked(getTokenUsagePricing).mockResolvedValue(pricingConfig)

    renderPage()

    const nav = await screen.findByRole('navigation')
    // 侧栏只保留五个区块锚点：分组标题、onboarding 入口与 workspace
    // 指引均已退役。
    for (const label of [
      'Studio Agent 管理',
      '外部服务连接',
      '基础设施连接',
      '实例设置',
      '模型定价',
    ]) {
      expect(
        within(nav).getByRole('button', { name: label })
      ).toBeInTheDocument()
    }
    expect(within(nav).queryByText('全局（实例级）')).not.toBeInTheDocument()
    expect(within(nav).queryByText('Workspace 级')).not.toBeInTheDocument()
    expect(
      within(nav).queryByRole('button', { name: '全局初始化清单' })
    ).not.toBeInTheDocument()
    // 默认高亮第一节（Studio Agent 管理）
    expect(
      within(nav).getByRole('button', { name: 'Studio Agent 管理' })
    ).toHaveAttribute('aria-current', 'true')
  })

  it('scrolls to the section named by the URL hash on mount', async () => {
    vi.mocked(getTokenUsagePricing).mockResolvedValue(pricingConfig)
    // jsdom 未实现 scrollIntoView，stub 掉以观察锚点滚动
    const scrollIntoView = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoView

    renderPage('#instance-settings')
    await screen.findByRole('navigation')

    const target = document.getElementById('instance-settings')
    expect(target).not.toBeNull()
    expect(scrollIntoView).toHaveBeenCalledTimes(1)
    expect(scrollIntoView).toHaveBeenCalledWith()
    // 只滚目标 section，不把所有 section 各滚一遍。
    const callsOnSections = scrollIntoView.mock.instances.length
    expect(callsOnSections).toBeLessThanOrEqual(1)
  })

  it('ignores hashes that do not name a settings section', async () => {
    vi.mocked(getTokenUsagePricing).mockResolvedValue(pricingConfig)
    const scrollIntoView = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoView

    renderPage('#not-a-section')
    await screen.findByRole('navigation')

    expect(scrollIntoView).not.toHaveBeenCalled()
  })

  it('moves the active nav item on click', async () => {
    vi.mocked(getTokenUsagePricing).mockResolvedValue(pricingConfig)
    // jsdom 未实现 scrollIntoView，stub 掉以覆盖点击路径
    const scrollIntoView = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoView

    renderPage()

    const nav = await screen.findByRole('navigation')
    fireEvent.click(within(nav).getByRole('button', { name: '外部服务连接' }))

    expect(scrollIntoView).toHaveBeenCalled()
    expect(
      within(nav).getByRole('button', { name: '外部服务连接' })
    ).toHaveAttribute('aria-current', 'true')
    expect(
      within(nav).getByRole('button', { name: '模型定价' })
    ).not.toHaveAttribute('aria-current')
  })

  it('renders the instance settings section with advanced groups collapsed', async () => {
    vi.mocked(getTokenUsagePricing).mockResolvedValue(pricingConfig)

    renderPage()

    expect(await screen.findByText('实例设置')).toBeInTheDocument()
    // 材料组（业务参数）直接可见；调优组默认折叠。
    expect(await screen.findByLabelText('材料保留天数（0 关闭）')).toHaveValue(
      0
    )
    expect(screen.queryByLabelText('日志保留天数')).not.toBeInTheDocument()
    expect(screen.getByText('保存后立即生效，无需重启')).toBeInTheDocument()
  })

  it('saves edited pricing via the card-local save button', async () => {
    vi.mocked(getTokenUsagePricing).mockResolvedValue(pricingConfig)
    vi.mocked(updateTokenUsagePricing).mockImplementation(async (payload) => ({
      currency: payload.currency,
      pricing: payload.pricing,
    }))

    renderPage()
    await screen.findByDisplayValue('model-a')

    fireEvent.change(screen.getByLabelText('output-rate-0'), {
      target: { value: '20' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存模型定价' }))

    await waitFor(() => {
      expect(updateTokenUsagePricing).toHaveBeenCalledWith({
        currency: 'CNY',
        pricing: [
          {
            provider: 'gateway',
            model: 'model-a',
            input_per_1m: 3,
            output_per_1m: 20,
            cache_read_per_1m: 0.6,
          },
        ],
      })
    })
    // Baseline updated: the card-local save button is disabled again.
    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: '保存模型定价' })
      ).toBeDisabled()
    })
  })

  it('rejects invalid rates before saving', async () => {
    vi.mocked(getTokenUsagePricing).mockResolvedValue(pricingConfig)

    renderPage()
    await screen.findByDisplayValue('model-a')

    fireEvent.change(screen.getByLabelText('input-rate-0'), {
      target: { value: '-1' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存模型定价' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      '费率必须是不小于 0 的数字'
    )
    expect(updateTokenUsagePricing).not.toHaveBeenCalled()
  })

  it('shows a no-permission hint for non-admin users', () => {
    useAuthStore.setState({ user: memberUser })

    renderPage()

    expect(screen.getByText(/无权限访问/)).toBeInTheDocument()
    expect(getTokenUsagePricing).not.toHaveBeenCalled()
  })
})
