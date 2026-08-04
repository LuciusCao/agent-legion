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

// 摘要卡数据由后端 summary 提供（分钟级口径，不随窗口切换），与 buckets 解耦。
function summary(overrides = {}) {
  return {
    online_workers: 3,
    active_executions: 2,
    recent_hour_tokens: {
      input_tokens: 400,
      output_tokens: 200,
      cache_read_tokens: 40,
      total_tokens: 640,
    },
    recent_hour_runs: {
      completed: 5,
      failed: 1,
      duration_p50_seconds: 42,
      duration_p95_seconds: 185,
    },
    ...overrides,
  }
}

const mockFetchOpsMetrics = vi.fn((params: { granularity: string }) =>
  Promise.resolve({
    granularity: params.granularity,
    buckets: [bucket(4), bucket(3), bucket(2), bucket(1)],
    summary: summary(),
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

    // 摘要元素首帧就以占位符 '-' 渲染，必须等内容到位而不是等元素出现，
    // 否则并行高负载下断言会抢在 fetch resolve 之前执行。
    await waitFor(() =>
      expect(screen.getByTestId('online-workers-summary')).toHaveTextContent(
        '3'
      )
    )
    expect(screen.getByTestId('active-executions-summary')).toHaveTextContent(
      '2'
    )
    // 近 1 小时 Token：来自 summary.recent_hour_tokens
    expect(screen.getByTestId('hourly-tokens-summary')).toHaveTextContent('640')
    // 细分：输入 400 · 输出 200 · 缓存读 40
    expect(screen.getByText(/输入 400/)).toBeInTheDocument()
    expect(screen.getByText(/输出 200/)).toBeInTheDocument()
    expect(screen.getByText(/缓存读 40/)).toBeInTheDocument()
    // 近 1 小时 Agent Runs：完成数、失败数与 p50/p95 耗时
    expect(screen.getByTestId('hourly-runs-summary')).toHaveTextContent(
      '完成 5'
    )
    expect(screen.getByText(/失败 1/)).toBeInTheDocument()
    expect(screen.getByText(/p50 42s/)).toBeInTheDocument()
    expect(screen.getByText(/p95 3m 05s/)).toBeInTheDocument()
    expect(
      screen.getByRole('img', { name: '在线 Worker 与活跃执行趋势' })
    ).toBeInTheDocument()
    expect(
      screen.getByRole('img', { name: 'Token 吞吐量趋势' })
    ).toBeInTheDocument()
    expect(mockFetchOpsMetrics).toHaveBeenCalledWith({
      granularity: '6h',
    })
  })

  it('refetches with the new window when granularity changes', async () => {
    const user = userEvent.setup()
    render(<MonitoringPanel />)

    await screen.findByTestId('online-workers-summary')
    await user.click(screen.getByRole('button', { name: '近 30 天' }))

    await waitFor(() => {
      expect(mockFetchOpsMetrics).toHaveBeenCalledWith({
        granularity: '30d',
      })
    })
    await user.click(screen.getByRole('button', { name: '近 24 小时' }))
    await waitFor(() => {
      expect(mockFetchOpsMetrics).toHaveBeenCalledWith({
        granularity: '24h',
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
        granularity: '6h',
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

  it('keeps summary cards stable when granularity changes', async () => {
    const user = userEvent.setup()
    render(<MonitoringPanel />)

    await waitFor(() =>
      expect(screen.getByTestId('hourly-tokens-summary')).toHaveTextContent(
        '640'
      )
    )
    await user.click(screen.getByRole('button', { name: '近 30 天' }))

    await waitFor(() => {
      expect(mockFetchOpsMetrics).toHaveBeenCalledWith({
        granularity: '30d',
      })
    })
    // 摘要卡读取后端 summary，与窗口粒度无关，切换后数值不变。
    expect(screen.getByTestId('online-workers-summary')).toHaveTextContent('3')
    expect(screen.getByTestId('active-executions-summary')).toHaveTextContent(
      '2'
    )
    expect(screen.getByTestId('hourly-tokens-summary')).toHaveTextContent('640')
    expect(screen.getByTestId('hourly-runs-summary')).toHaveTextContent(
      '完成 5'
    )
  })

  it('renders placeholder when no runs completed in the recent hour', async () => {
    mockFetchOpsMetrics.mockResolvedValueOnce({
      granularity: '6h',
      buckets: [],
      summary: summary({
        recent_hour_runs: {
          completed: 0,
          failed: 0,
          duration_p50_seconds: null,
          duration_p95_seconds: null,
        },
      }),
    })

    render(<MonitoringPanel />)

    await waitFor(() =>
      expect(screen.getByTestId('hourly-runs-summary')).toHaveTextContent(
        '完成 0'
      )
    )
    expect(screen.getByText(/失败 0 · p50 - · p95 -/)).toBeInTheDocument()
  })

  it('renders a zero-filled fixed window when no buckets', async () => {
    mockFetchOpsMetrics.mockResolvedValueOnce({
      granularity: '6h',
      buckets: [],
      summary: summary({
        online_workers: null,
        active_executions: null,
        recent_hour_tokens: {
          input_tokens: 0,
          output_tokens: 0,
          cache_read_tokens: 0,
          total_tokens: 0,
        },
      }),
    })

    render(<MonitoringPanel />)

    // 固定时间窗下空数据渲染为零值折线，而不是“暂无数据”占位
    await waitFor(() =>
      expect(screen.getByTestId('hourly-tokens-summary')).toHaveTextContent('0')
    )
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument()
    expect(
      screen.getByRole('img', { name: 'Token 吞吐量趋势' })
    ).toBeInTheDocument()
  })
})
