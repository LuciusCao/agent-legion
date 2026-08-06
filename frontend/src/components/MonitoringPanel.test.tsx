import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import { TestQueryProvider } from '../testing/testQueryClient'
import { MonitoringPanel } from './MonitoringPanel'

// react-query 需要 QueryClientProvider；每个用例独立 client（retry 关闭）。
function renderPanel(props: { workspaceId?: string } = {}) {
  return render(
    <TestQueryProvider>
      <MonitoringPanel {...props} />
    </TestQueryProvider>
  )
}

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
    queued: 12,
    queued_max: 20,
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
    queue: {
      queued: 12,
      oldest_queued_at: null,
      recent_hour_unclaimable_failed: 0,
    },
    queue_alert: null,
    ...overrides,
  }
}

// 主数据与队列深度图同参数时共享 queryKey（合并为一次请求），切换 Worker 过滤
// 或 30s 轮询都会再次拉取；自定义响应必须用持久实现并在 afterEach 复原，不能
// 用 Once 系列。
function defaultMetricsResponse(params: { granularity: string }) {
  return Promise.resolve({
    granularity: params.granularity,
    buckets: [bucket(4), bucket(3), bucket(2), bucket(1)],
    summary: summary(),
  })
}

const mockFetchOpsMetrics = vi.fn(defaultMetricsResponse)

afterEach(() => {
  mockFetchOpsMetrics.mockImplementation(defaultMetricsResponse)
})

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
    renderPanel()

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
    // 队列摘要卡与队列深度图（全局指标）
    expect(screen.getByTestId('queue-depth-summary')).toHaveTextContent('12')
    expect(screen.getByTestId('queue-sweeper-summary')).toHaveTextContent('0')
    expect(
      screen.getByRole('img', { name: 'Agent 执行队列深度趋势' })
    ).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(mockFetchOpsMetrics).toHaveBeenCalledWith({
      granularity: '6h',
    })
  })

  it('renders the blocked queue alert with the skip-reason histogram', async () => {
    mockFetchOpsMetrics.mockResolvedValue({
      granularity: '6h',
      buckets: [bucket(1)],
      summary: summary({
        queue: {
          queued: 33412,
          oldest_queued_at: new Date(Date.now() - 18 * 3600_000).toISOString(),
          recent_hour_unclaimable_failed: 136,
        },
        queue_alert: {
          kind: 'blocked',
          at: new Date().toISOString(),
          reasons: { capability_or_model_mismatch: 8 },
        },
      }),
    })

    renderPanel()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('队列堵塞')
    expect(alert).toHaveTextContent('33,412 条请求排队中')
    expect(alert).toHaveTextContent('model/capability 不匹配 ×8')
    // 队首最老超过 1 小时标红（18h）
    expect(screen.getByText('18.0h')).toBeInTheDocument()
    expect(screen.getByTestId('queue-sweeper-summary')).toHaveTextContent('136')
  })

  it('renders the stalled queue alert without claim skip reasons', async () => {
    mockFetchOpsMetrics.mockResolvedValue({
      granularity: '6h',
      buckets: [bucket(1)],
      summary: summary({
        queue_alert: { kind: 'stalled', at: null, reasons: {} },
      }),
    })

    renderPanel()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('队列停滞')
    expect(alert).toHaveTextContent('领取开关关闭')
  })

  it('refetches with the new window when granularity changes', async () => {
    const user = userEvent.setup()
    renderPanel()

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
    renderPanel()

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
    mockFetchOpsMetrics.mockRejectedValue(new Error('network error'))

    renderPanel()

    expect(
      await screen.findByText('监控数据加载失败：network error')
    ).toBeInTheDocument()
  })

  it('keeps summary cards stable when granularity changes', async () => {
    const user = userEvent.setup()
    renderPanel()

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
    mockFetchOpsMetrics.mockResolvedValue({
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

    renderPanel()

    await waitFor(() =>
      expect(screen.getByTestId('hourly-runs-summary')).toHaveTextContent(
        '完成 0'
      )
    )
    expect(screen.getByText(/失败 0 · p50 - · p95 -/)).toBeInTheDocument()
  })

  it('renders a zero-filled fixed window when no buckets', async () => {
    mockFetchOpsMetrics.mockResolvedValue({
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

    renderPanel()

    // 固定时间窗下空数据渲染为零值折线，而不是“暂无数据”占位
    await waitFor(() =>
      expect(screen.getByTestId('hourly-tokens-summary')).toHaveTextContent('0')
    )
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument()
    expect(
      screen.getByRole('img', { name: 'Token 吞吐量趋势' })
    ).toBeInTheDocument()
  })

  it('scopes the panel to the workspace when workspaceId is given', async () => {
    render(
      <MemoryRouter>
        <MonitoringPanel workspaceId="ops-ws" />
      </MemoryRouter>
    )

    await waitFor(() =>
      expect(screen.getByTestId('queue-depth-summary')).toHaveTextContent('12')
    )
    // 主数据请求带 workspace 过滤
    expect(mockFetchOpsMetrics).toHaveBeenCalledWith({
      granularity: '6h',
      workspace_id: 'ops-ws',
    })
    // fleet-only 内容隐藏：在线 Worker 卡片与 Worker 过滤器
    expect(
      screen.queryByTestId('online-workers-summary')
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('combobox', { name: '选择 Worker' })
    ).not.toBeInTheDocument()
    // 副标题标注 workspace；全局监控入口不在 ws 视图（挪到首页 admin 菜单）
    expect(screen.getByText(/workspace「ops-ws」/)).toBeInTheDocument()
    expect(
      screen.queryByRole('link', { name: '查看全局监控' })
    ).not.toBeInTheDocument()
  })
})
