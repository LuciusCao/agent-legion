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

vi.mock('../api/skillSources', () => ({
  getSkillSources: vi.fn().mockResolvedValue({ skills: [] }),
  updateSkillSource: vi.fn(),
  relockSkillSources: vi.fn(),
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
  workflows: { enabled: true },
  agent_workers: { max_archive_bytes: 104857600, min_protocol_version: 2 },
  openclaw: {
    cwd: '.',
  },
  skills_root: '~/.agents/skills',
}

function renderPage() {
  return render(
    <MemoryRouter>
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

  it('renders the sidebar nav with entries for each section', async () => {
    vi.mocked(getTokenUsagePricing).mockResolvedValue(pricingConfig)

    renderPage()

    const nav = await screen.findByRole('navigation')
    for (const label of [
      '模型定价',
      '实例设置',
      '外部服务连接',
      'Skill 源管理',
      'Studio Agent 管理',
    ]) {
      expect(
        within(nav).getByRole('button', { name: label })
      ).toBeInTheDocument()
    }
    // 默认高亮第一节
    expect(
      within(nav).getByRole('button', { name: '模型定价' })
    ).toHaveAttribute('aria-current', 'true')
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

  it('renders the instance settings section', async () => {
    vi.mocked(getTokenUsagePricing).mockResolvedValue(pricingConfig)

    renderPage()

    expect(await screen.findByText('实例设置')).toBeInTheDocument()
    // 实例设置表单异步加载，label 需等待（导航按钮会提前匹配标题文本）
    expect(await screen.findByLabelText('日志保留天数')).toHaveValue(30)
    expect(screen.getByText(/需重启服务才能生效/)).toBeInTheDocument()
    // 材料 TTL 字段级 hint：保存即生效，且 input 带 max 上界。
    expect(screen.getByText('保存后立即生效，无需重启')).toBeInTheDocument()
    expect(screen.getByLabelText('材料保留天数（0 关闭）')).toHaveAttribute(
      'max',
      '36500'
    )
  })

  it('keeps the save button disabled until the form is dirty', async () => {
    vi.mocked(getTokenUsagePricing).mockResolvedValue(pricingConfig)

    renderPage()
    await screen.findByDisplayValue('model-a')

    expect(screen.getByLabelText('保存')).toBeDisabled()

    fireEvent.change(screen.getByLabelText('output-rate-0'), {
      target: { value: '20' },
    })
    expect(screen.getByLabelText('保存')).toBeEnabled()
  })

  it('saves edited pricing via the AppBar save button', async () => {
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
    fireEvent.click(screen.getByLabelText('保存'))

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
    // Baseline updated: the form is clean again after a successful save.
    await waitFor(() => {
      expect(screen.getByLabelText('保存')).toBeDisabled()
    })
  })

  it('adds and removes rows', async () => {
    vi.mocked(getTokenUsagePricing).mockResolvedValue(pricingConfig)

    renderPage()
    await screen.findByDisplayValue('model-a')

    fireEvent.click(screen.getByText('添加一行'))
    expect(screen.getByTestId('pricing-row-1')).toBeInTheDocument()

    fireEvent.click(
      screen.getByTestId('pricing-row-1').querySelector('button') as HTMLElement
    )
    expect(screen.queryByTestId('pricing-row-1')).not.toBeInTheDocument()
  })

  it('rejects invalid rates before saving', async () => {
    vi.mocked(getTokenUsagePricing).mockResolvedValue(pricingConfig)

    renderPage()
    await screen.findByDisplayValue('model-a')

    fireEvent.change(screen.getByLabelText('input-rate-0'), {
      target: { value: '-1' },
    })
    fireEvent.click(screen.getByLabelText('保存'))

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
