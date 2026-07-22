import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { TokenUsageRunDetail } from './TokenUsageRunDetail'
import * as jobApi from '../jobApi'
import type { NodeRun } from '../jobTypes'

vi.mock('../jobApi', () => ({
  fetchRunTokenUsage: vi.fn(),
}))

const mockFetchRunTokenUsage = vi.mocked(jobApi.fetchRunTokenUsage)

function makeRun(status: string): NodeRun {
  return {
    id: 1,
    job_id: 'j1',
    node_key: 'extract',
    status,
    command_json: '',
    error_message: '',
    log_path: '',
    run_dir: '',
    runner: 'pi',
    session_dir: '',
    started_at: '2026-07-22 02:00:00',
  }
}

const mockUsageResponse = {
  job_id: 'j1',
  run_id: 1,
  usage: {
    node_run_id: 1,
    node_key: 'extract',
    provider: 'openai',
    model: 'gpt-4o',
    skill_version: 'v1.2.3',
    message_count: 5,
    input_tokens: 1000,
    output_tokens: 200,
    cache_read_tokens: 50,
    total_tokens: 1250,
    cost: {
      input: 0.005,
      output: 0.003,
      cache_read: 0.0001,
      total: 0.0081,
      currency: 'USD',
    },
    pricing_missing: false,
    is_complete: true,
    usage_source: 'events_jsonl',
  },
  reason: null,
}

describe('TokenUsageRunDetail', () => {
  beforeEach(() => {
    mockFetchRunTokenUsage.mockReset()
  })

  it('shows compact token summary after loading', async () => {
    mockFetchRunTokenUsage.mockResolvedValue(mockUsageResponse)

    render(<TokenUsageRunDetail jobId="j1" run={makeRun('completed')} />)
    await waitFor(() => {
      expect(mockFetchRunTokenUsage).toHaveBeenCalledWith('j1', 1)
    })

    expect(screen.getByText(/Token: 1,250/)).toBeInTheDocument()
    expect(screen.getByText(/USD 0.0081/)).toBeInTheDocument()
  })

  it('expands to show provider, model, skill version and breakdowns', async () => {
    mockFetchRunTokenUsage.mockResolvedValue(mockUsageResponse)

    render(<TokenUsageRunDetail jobId="j1" run={makeRun('completed')} />)
    await waitFor(() => {
      expect(mockFetchRunTokenUsage).toHaveBeenCalledWith('j1', 1)
    })

    fireEvent.click(screen.getByLabelText('Token 用量'))

    expect(screen.getByText('openai / gpt-4o')).toBeInTheDocument()
    expect(screen.getByText('v1.2.3')).toBeInTheDocument()
    expect(screen.getByText('5')).toBeInTheDocument()

    expect(screen.getByText('1,000')).toBeInTheDocument()
    expect(screen.getByText('200')).toBeInTheDocument()
    expect(screen.getByText('50')).toBeInTheDocument()

    expect(screen.getAllByText('USD 0.0050').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('USD 0.0030').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('USD 0.0001').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('USD 0.0081').length).toBeGreaterThanOrEqual(1)
  })

  it('shows incomplete warning when usage is not complete', async () => {
    mockFetchRunTokenUsage.mockResolvedValue({
      ...mockUsageResponse,
      usage: { ...mockUsageResponse.usage, is_complete: false },
    })

    render(<TokenUsageRunDetail jobId="j1" run={makeRun('completed')} />)
    await waitFor(() => {
      expect(mockFetchRunTokenUsage).toHaveBeenCalledWith('j1', 1)
    })

    fireEvent.click(screen.getByLabelText('Token 用量'))

    expect(screen.getByText('部分 token 数据可能不完整')).toBeInTheDocument()
  })

  it('shows missing usage message with backend reason', async () => {
    mockFetchRunTokenUsage.mockResolvedValue({
      job_id: 'j1',
      run_id: 1,
      usage: null,
      reason: 'no token usage recorded for run',
    })

    render(<TokenUsageRunDetail jobId="j1" run={makeRun('completed')} />)
    await waitFor(() => {
      expect(mockFetchRunTokenUsage).toHaveBeenCalledWith('j1', 1)
    })

    fireEvent.click(screen.getByLabelText('Token 用量'))

    expect(
      screen.getByText('no token usage recorded for run')
    ).toBeInTheDocument()
  })

  it('shows missing usage message when fetch fails', async () => {
    mockFetchRunTokenUsage.mockRejectedValue(new Error('network error'))

    render(<TokenUsageRunDetail jobId="j1" run={makeRun('completed')} />)
    await waitFor(() => {
      expect(mockFetchRunTokenUsage).toHaveBeenCalledWith('j1', 1)
    })

    expect(screen.getByText('无 token 数据')).toBeInTheDocument()
  })

  it('renders nothing but the compact button while loading', () => {
    mockFetchRunTokenUsage.mockImplementation(() => new Promise(() => {}))

    render(<TokenUsageRunDetail jobId="j1" run={makeRun('completed')} />)

    expect(screen.getByText('Token 用量...')).toBeInTheDocument()
  })

  it('refetches and shows new data when status changes from running to completed', async () => {
    mockFetchRunTokenUsage.mockResolvedValue({
      job_id: 'j1',
      run_id: 1,
      usage: null,
      reason: null,
    })

    const { rerender } = render(
      <TokenUsageRunDetail jobId="j1" run={makeRun('running')} />
    )
    await waitFor(() => {
      expect(screen.getByText('无 token 数据')).toBeInTheDocument()
    })

    mockFetchRunTokenUsage.mockResolvedValue(mockUsageResponse)
    rerender(<TokenUsageRunDetail jobId="j1" run={makeRun('completed')} />)

    await waitFor(() => {
      expect(screen.getByText(/Token: 1,250/)).toBeInTheDocument()
    })
    expect(mockFetchRunTokenUsage).toHaveBeenCalledTimes(2)
    expect(mockFetchRunTokenUsage).toHaveBeenLastCalledWith('j1', 1)
  })
})
