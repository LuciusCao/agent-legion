import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { TokenUsageJobPanel } from './TokenUsageJobPanel'

const mockFetchJobTokenUsage = vi.fn()

vi.mock('../jobApi', () => ({
  fetchJobTokenUsage: (...args: [string]) => mockFetchJobTokenUsage(...args),
}))

const baseResponse = {
  job_id: 'j1',
  currency: 'CNY',
  runs: [
    {
      run_id: 1,
      node_key: 'extract',
      status: 'completed',
      usage: {
        provider: 'gateway',
        model: 'gateway/your-model-a',
        skill_version: 'v1',
        message_count: 4,
        input_tokens: 100,
        output_tokens: 50,
        cache_read_tokens: 10,
        total_tokens: 160,
        cost: {
          input: 0.1,
          output: 0.2,
          cache_read: 0.01,
          total: 0.31,
          currency: 'CNY',
        },
        is_complete: true,
        pricing_missing: false,
      },
      reason: null,
    },
  ],
  total: {
    message_count: 4,
    input_tokens: 100,
    output_tokens: 50,
    cache_read_tokens: 10,
    total_tokens: 160,
    cost: {
      input: 0.1,
      output: 0.2,
      cache_read: 0.01,
      total: 0.31,
      currency: 'CNY',
    },
    pricing_missing: false,
  },
  runs_with_usage: 1,
  runs_without_usage: 0,
}

describe('TokenUsageJobPanel', () => {
  beforeEach(() => {
    mockFetchJobTokenUsage.mockReset()
    mockFetchJobTokenUsage.mockResolvedValue(baseResponse)
  })

  it('fetches and renders total summary', async () => {
    render(<TokenUsageJobPanel jobId="j1" />)
    await waitFor(() => {
      expect(mockFetchJobTokenUsage).toHaveBeenCalledWith('j1')
    })
    expect(await screen.findAllByText('160')).toHaveLength(2)
    expect(screen.getAllByText('¥ 0.3100')).toHaveLength(2)
  })

  it('renders run rows', async () => {
    render(<TokenUsageJobPanel jobId="j1" />)
    expect(await screen.findByText('extract')).toBeInTheDocument()
    expect(screen.getByText('completed')).toBeInTheDocument()
  })

  it('shows reason for runs without usage', async () => {
    mockFetchJobTokenUsage.mockResolvedValue({
      ...baseResponse,
      runs: [
        ...baseResponse.runs,
        {
          run_id: 2,
          node_key: 'review',
          status: 'failed',
          usage: null,
          reason: '未记录到 token 用量',
        },
      ],
      runs_with_usage: 1,
      runs_without_usage: 1,
    })
    render(<TokenUsageJobPanel jobId="j1" />)
    expect(await screen.findByText('未记录到 token 用量')).toBeInTheDocument()
  })
})
