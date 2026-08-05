import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Routes, Route } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import { TokenUsagePage } from './TokenUsagePage'

vi.mock('../api', () => ({
  fetchWorkspaces: vi.fn().mockResolvedValue({
    workspaces: [{ id: 'ws1', name: '测试空间' }],
  }),
}))

vi.mock('../api/tokenUsage', () => ({
  fetchWorkspaceTokenUsage: vi.fn().mockResolvedValue({
    workspace_id: 'ws1',
    currency: 'CNY',
    summary: {
      message_count: 0,
      input_tokens: 0,
      output_tokens: 0,
      cache_read_tokens: 0,
      total_tokens: 0,
      cost: null,
      pricing_missing: true,
    },
    runs_with_usage: 0,
    runs_without_usage: 0,
    groups: [],
  }),
}))

function renderPage(initialEntries = ['/workspaces/ws1/token-usage']) {
  return render(
    <MemoryRouter
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      initialEntries={initialEntries}
    >
      <Routes>
        <Route
          path="/workspaces/:workspaceId/token-usage"
          element={<TokenUsagePage />}
        />
      </Routes>
    </MemoryRouter>
  )
}

describe('TokenUsagePage', () => {
  it('renders page title with workspace name', async () => {
    renderPage()
    expect(
      await screen.findByText('测试空间 / Token 使用分析')
    ).toBeInTheDocument()
  })

  it('renders back button to workspace', async () => {
    renderPage()
    expect(await screen.findByTestId('app-bar-back')).toBeInTheDocument()
  })
})
