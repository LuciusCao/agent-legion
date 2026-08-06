import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import GlobalSettingsPage from './GlobalSettingsPage'
import { useAuthStore } from '../stores/authStore'
import type { UserResponse } from '../api/authApi'
import {
  getTokenUsagePricing,
  updateTokenUsagePricing,
} from '../api/tokenUsagePricing'
import type { TokenUsagePricingConfigResponse } from '../api/tokenUsagePricing'

vi.mock('../api/tokenUsagePricing', () => ({
  getTokenUsagePricing: vi.fn(),
  updateTokenUsagePricing: vi.fn(),
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

function renderPage() {
  return render(
    <MemoryRouter>
      <GlobalSettingsPage />
    </MemoryRouter>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
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
    expect(screen.getByText('模型定价')).toBeInTheDocument()
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
