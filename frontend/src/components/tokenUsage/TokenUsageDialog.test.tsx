import { render, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { TokenUsageDialog } from './TokenUsageDialog'
import { useUiStore } from '../../stores/uiStore'
import { TestQueryProvider } from '../../testing/testQueryClient'

function renderWithClient(ui: ReactElement) {
  return render(<TestQueryProvider>{ui}</TestQueryProvider>)
}

vi.mock('../../api/tokenUsage', () => ({
  fetchWorkspaceTokenUsage: vi.fn(() =>
    Promise.resolve({
      workspace_id: 'ws1',
      currency: 'CNY',
      summary: {
        message_count: 0,
        input_tokens: 0,
        output_tokens: 0,
        cache_read_tokens: 0,
        total_tokens: 0,
        cost: { input: 0, output: 0, cache_read: 0, total: 0, currency: 'CNY' },
        pricing_missing: false,
      },
      groups: [],
      runs_with_usage: 0,
      runs_without_usage: 0,
    })
  ),
}))

vi.mock('../../api/jobApi', () => ({
  fetchJobTokenUsage: vi.fn(() =>
    Promise.resolve({
      job_id: 'j1',
      currency: 'CNY',
      runs: [],
      total: {
        message_count: 0,
        input_tokens: 0,
        output_tokens: 0,
        cache_read_tokens: 0,
        total_tokens: 0,
        cost: null,
        pricing_missing: false,
      },
      runs_with_usage: 0,
      runs_without_usage: 0,
    })
  ),
}))

describe('TokenUsageDialog', () => {
  beforeEach(() => {
    useUiStore.setState({ tokenUsageDialogOpen: false })
  })

  it('does not render content when closed', () => {
    renderWithClient(<TokenUsageDialog scope="workspace" workspaceId="ws1" />)
    expect(
      screen.queryByText('Workspace Token 使用分析')
    ).not.toBeInTheDocument()
  })

  it('renders workspace title when open', async () => {
    useUiStore.setState({ tokenUsageDialogOpen: true })
    renderWithClient(<TokenUsageDialog scope="workspace" workspaceId="ws1" />)
    expect(
      await screen.findByText('Workspace Token 使用分析')
    ).toBeInTheDocument()
  })

  it('renders job title when open', async () => {
    useUiStore.setState({ tokenUsageDialogOpen: true })
    renderWithClient(<TokenUsageDialog scope="job" jobId="j1" />)
    expect(await screen.findByText('Job Token 使用分析')).toBeInTheDocument()
  })
})
