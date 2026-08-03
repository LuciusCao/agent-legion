import {
  render,
  screen,
  waitFor,
  fireEvent,
  within,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { TokenUsagePanel } from './TokenUsagePanel'

const mockFetchWorkspaceTokenUsage = vi.fn(
  (_workspaceId: string, params: URLSearchParams) => {
    const groupBy = params.get('group_by') || 'node'
    const isModel = groupBy === 'model'
    const isVersion = groupBy === 'skill_version'
    const isComposite = groupBy === 'node_skill_version'
    return Promise.resolve({
      workspace_id: 'ws1',
      currency: 'CNY',
      summary: {
        message_count: 2,
        input_tokens: 150,
        output_tokens: 75,
        cache_read_tokens: 25,
        total_tokens: 250,
        cost: {
          input: 0.1,
          output: 0.2,
          cache_read: 0.01,
          total: 0.31,
          currency: 'CNY',
        },
        pricing_missing: false,
      },
      runs_with_usage: 2,
      runs_without_usage: 2,
      groups: [
        {
          group_key: isModel
            ? 'gateway/your-model-a'
            : isVersion
              ? 'v1.2.3'
              : isComposite
                ? 'generate_key_info / v1.2.3'
                : 'generate_key_info',
          node_key: 'generate_key_info',
          provider: 'gateway',
          model: 'gateway/your-model-a',
          skill_version: 'v1.2.3',
          runs: 2,
          avg_input_tokens: 75,
          avg_output_tokens: 37.5,
          avg_cache_read_tokens: 12.5,
          avg_total_tokens: 125,
          total_input_tokens: 150,
          total_output_tokens: 75,
          total_cache_read_tokens: 25,
          total_tokens: 250,
          total_cost: 0.31,
          avg_cost: 0.155,
          pricing_missing: false,
          coverage: 0.5,
        },
      ],
    })
  }
)

vi.mock('../../api/tokenUsage', () => ({
  fetchWorkspaceTokenUsage: (...args: [string, URLSearchParams]) =>
    mockFetchWorkspaceTokenUsage(...args),
}))

describe('TokenUsagePanel', () => {
  it('renders workspace usage summary and group rows', async () => {
    render(<TokenUsagePanel workspaceId="ws1" />)

    const table = await screen.findByRole('table')
    expect(
      await within(table).findByText('generate_key_info')
    ).toBeInTheDocument()
    expect(screen.getByTestId('coverage-summary')).toHaveTextContent('50%')
    expect(screen.getByTestId('total-tokens-summary')).toHaveTextContent('250')
    expect(screen.getByTestId('total-cost-summary')).toHaveTextContent(
      '¥ 0.3100'
    )
    expect(screen.getByText('最高成本节点')).toBeInTheDocument()
  })

  it('switches group dimension', async () => {
    const user = userEvent.setup()
    render(<TokenUsagePanel workspaceId="ws1" />)

    const table = await screen.findByRole('table')
    expect(
      await within(table).findByText('generate_key_info')
    ).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: '按模型' }))

    await waitFor(() => {
      expect(
        within(table).getByText('gateway/your-model-a')
      ).toBeInTheDocument()
    })
  })

  it('expands a group to show token and cost breakdown', async () => {
    render(<TokenUsagePanel workspaceId="ws1" />)

    const table = await screen.findByRole('table')
    expect(
      await within(table).findByText('generate_key_info')
    ).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '展开' }))

    await waitFor(() => {
      expect(screen.getByText('Token 明细')).toBeInTheDocument()
    })
    expect(screen.getByText('成本明细')).toBeInTheDocument()
    expect(screen.getByText('Input')).toBeInTheDocument()
    expect(screen.getByText('150')).toBeInTheDocument()
  })

  it('applies node filter', async () => {
    const user = userEvent.setup()
    render(<TokenUsagePanel workspaceId="ws1" />)

    const table = await screen.findByRole('table')
    expect(
      await within(table).findByText('generate_key_info')
    ).toBeInTheDocument()

    const nodeFilter = screen.getByLabelText('节点')
    await user.click(nodeFilter)
    await user.click(screen.getByRole('option', { name: 'generate_key_info' }))

    await waitFor(() => {
      expect(mockFetchWorkspaceTokenUsage).toHaveBeenCalledWith(
        'ws1',
        expect.any(URLSearchParams)
      )
    })
    const lastCall = mockFetchWorkspaceTokenUsage.mock.calls[
      mockFetchWorkspaceTokenUsage.mock.calls.length - 1
    ] as [string, URLSearchParams]
    expect(lastCall[1].get('node_key')).toBe('generate_key_info')
  })

  it('shows empty state when no groups', async () => {
    mockFetchWorkspaceTokenUsage.mockResolvedValueOnce({
      workspace_id: 'ws1',
      currency: 'CNY',
      summary: {
        message_count: 0,
        input_tokens: 0,
        output_tokens: 0,
        cache_read_tokens: 0,
        total_tokens: 0,
        cost: {
          input: 0,
          output: 0,
          cache_read: 0,
          total: 0,
          currency: 'CNY',
        },
        pricing_missing: false,
      },
      runs_with_usage: 0,
      runs_without_usage: 0,
      groups: [],
    })

    render(<TokenUsagePanel workspaceId="ws1" />)

    expect(await screen.findByText('暂无 token 统计')).toBeInTheDocument()
  })

  it('renders error message on fetch failure', async () => {
    mockFetchWorkspaceTokenUsage.mockRejectedValueOnce(
      new Error('network error')
    )

    render(<TokenUsagePanel workspaceId="ws1" />)

    expect(
      await screen.findByText('Token 统计加载失败：network error')
    ).toBeInTheDocument()
  })
})
