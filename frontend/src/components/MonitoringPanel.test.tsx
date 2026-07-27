import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { MonitoringPanel } from './MonitoringPanel'

// bucket_start 必须对齐到整分钟，否则在固定时间窗网格里匹配不上会被填零。
function bucket(offsetMinutes: number, overrides = {}) {
  const aligned =
    Math.floor((Date.now() - offsetMinutes * 60_000) / 60_000) * 60_000
  return {
    bucket_start: new Date(aligned).toISOString(),
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
      buckets: [bucket(4), bucket(3), bucket(2), bucket(1)],
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
    // 细分：输入 400 · 输出 200 · 缓存读 40
    expect(screen.getByText(/输入 400/)).toBeInTheDocument()
    expect(screen.getByText(/输出 200/)).toBeInTheDocument()
    expect(screen.getByText(/缓存读 40/)).toBeInTheDocument()
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

  it('renders a zero-filled fixed window when no buckets', async () => {
    mockFetchOpsMetrics.mockResolvedValueOnce({
      granularity: 'minute',
      buckets: [],
    })

    render(<MonitoringPanel />)

    // 固定时间窗下空数据渲染为零值折线，而不是“暂无数据”占位
    expect(
      await screen.findByTestId('hourly-tokens-summary')
    ).toHaveTextContent('0')
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument()
    expect(
      screen.getByRole('img', { name: 'Token 吞吐量趋势' })
    ).toBeInTheDocument()
  })
})
