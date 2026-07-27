import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { MonitoringPanel } from './MonitoringPanel'

const now = Date.now()

function bucket(offsetMinutes: number, overrides = {}) {
  return {
    bucket_start: new Date(now - offsetMinutes * 60_000).toISOString(),
    online_workers: 3,
    online_workers_max: 5,
    active_executions: 2,
    active_executions_max: 4,
    input_tokens: 100,
    output_tokens: 50,
    cache_read_tokens: 10,
    total_tokens: 160,
    ...overrides,
  }
}

const mockFetchOpsMetrics = vi.fn(
  (params: { granularity: string; hours?: number; days?: number }) =>
    Promise.resolve({
      granularity: params.granularity,
      buckets: [bucket(30), bucket(20), bucket(10), bucket(0)],
    })
)

vi.mock('../api/metrics', () => ({
  fetchOpsMetrics: (...args: [{ granularity: string }]) =>
    mockFetchOpsMetrics(...args),
}))

const mockListAgentWorkers = vi.fn(() =>
  Promise.resolve([
    { worker_id: 'worker-1', name: 'gpu-box-1', online: true },
    { worker_id: 'worker-2', name: '', online: false },
  ])
)

vi.mock('../api/workerTokens', () => ({
  listAgentWorkers: () => mockListAgentWorkers(),
}))

describe('MonitoringPanel', () => {
  it('renders stat cards and charts from fetched buckets', async () => {
    render(<MonitoringPanel />)

    expect(
      await screen.findByTestId('online-workers-summary')
    ).toHaveTextContent('3')
    expect(screen.getByTestId('active-executions-summary')).toHaveTextContent(
      '2'
    )
    // 近 1 小时：4 个 bucket 各 160 total tokens
    expect(screen.getByTestId('hourly-tokens-summary')).toHaveTextContent('640')
    expect(
      screen.getByRole('img', { name: '在线 Worker 与活跃执行趋势' })
    ).toBeInTheDocument()
    expect(
      screen.getByRole('img', { name: 'Token 吞吐量趋势' })
    ).toBeInTheDocument()
    expect(mockFetchOpsMetrics).toHaveBeenCalledWith({
      granularity: 'minute',
      hours: 6,
    })
  })

  it('refetches with the new window when granularity changes', async () => {
    const user = userEvent.setup()
    render(<MonitoringPanel />)

    await screen.findByTestId('online-workers-summary')
    await user.click(screen.getByRole('button', { name: '天 · 近 7 天' }))

    await waitFor(() => {
      expect(mockFetchOpsMetrics).toHaveBeenCalledWith({
        granularity: 'day',
        days: 7,
      })
    })
    await user.click(screen.getByRole('button', { name: '小时 · 近 24 小时' }))
    await waitFor(() => {
      expect(mockFetchOpsMetrics).toHaveBeenCalledWith({
        granularity: 'hour',
        hours: 24,
      })
    })
  })

  it('refetches with worker_id when a worker is selected', async () => {
    render(<MonitoringPanel />)

    await screen.findByTestId('online-workers-summary')

    const select = screen.getByRole('combobox', { name: '选择 Worker' })
    await act(async () => {
      fireEvent.mouseDown(select)
    })
    await waitFor(() => {
      expect(
        screen.getByRole('option', { name: 'gpu-box-1（在线）' })
      ).toBeInTheDocument()
      // name 为空时回退展示 worker_id，并标注离线
      expect(
        screen.getByRole('option', { name: 'worker-2（离线）' })
      ).toBeInTheDocument()
    })

    await act(async () => {
      fireEvent.click(screen.getByRole('option', { name: 'gpu-box-1（在线）' }))
    })

    await waitFor(() => {
      expect(mockFetchOpsMetrics).toHaveBeenCalledWith({
        granularity: 'minute',
        hours: 6,
        worker_id: 'worker-1',
      })
    })
  })

  it('renders error message on fetch failure', async () => {
    mockFetchOpsMetrics.mockRejectedValueOnce(new Error('network error'))

    render(<MonitoringPanel />)

    expect(
      await screen.findByText('监控数据加载失败：network error')
    ).toBeInTheDocument()
  })

  it('shows empty chart state when no buckets', async () => {
    mockFetchOpsMetrics.mockResolvedValueOnce({
      granularity: 'minute',
      buckets: [],
    })

    render(<MonitoringPanel />)

    await waitFor(() => {
      expect(screen.getAllByText('暂无数据')).toHaveLength(2)
    })
  })
})
