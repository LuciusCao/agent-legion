import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Routes, Route } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import CampaignsPage from './CampaignsPage'
import { useCampaigns } from '../hooks/useCampaigns'
import { makeCampaign } from '../components/campaign/testHelpers'

// 「批量任务」页的挂载测试（mock useCampaigns transport）：列表渲染与
// 加载/错误态的透传。路由挂载本身（WorkspaceLayout 子路由）由
// AppRoutes 的 pages 工厂 + WorkspacePageOutlet 既有行为保证。

vi.mock('../hooks/useCampaigns', () => ({
  useCampaigns: vi.fn(),
}))

const mockUseCampaigns = vi.mocked(useCampaigns)

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/workspaces/ws1/campaigns']}>
      <Routes>
        <Route
          path="/workspaces/:workspaceId/campaigns"
          element={<CampaignsPage />}
        />
      </Routes>
    </MemoryRouter>
  )
}

describe('CampaignsPage（批量任务）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the batch list from the polling hook', () => {
    mockUseCampaigns.mockReturnValue({
      data: { campaigns: [makeCampaign()] },
      isLoading: false,
      error: null,
    } as ReturnType<typeof useCampaigns>)
    renderPage()
    expect(screen.getByTestId('batch-list')).toBeInTheDocument()
    expect(screen.getByText('重跑 · 全部失败任务')).toBeInTheDocument()
    expect(screen.getByText('批量任务')).toBeInTheDocument()
  })

  it('shows the loading state while the first fetch is in flight', () => {
    mockUseCampaigns.mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as ReturnType<typeof useCampaigns>)
    renderPage()
    expect(document.querySelector('[role="progressbar"]')).not.toBeNull()
  })

  it('surfaces the fetch error', () => {
    mockUseCampaigns.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error('网络错误'),
    } as ReturnType<typeof useCampaigns>)
    renderPage()
    expect(
      screen.getByText(/批量任务列表加载失败：网络错误/)
    ).toBeInTheDocument()
  })
})
