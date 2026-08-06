import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { OpsGranularity } from '../api/metrics'
import { listAgentWorkers } from '../api/workerTokens'
import { queryKeys } from '../lib/queryKeys'
import { useOpsMetrics } from '../hooks/useOpsMetrics'
import { fillWindowBuckets } from '../lib/opsMetricsWindow'
import { lastNonNullBucket } from '../lib/opsMetricsBuckets'
import { fmt, fmtDuration, makeTimeFormatter } from '../lib/monitoringFormat'
import { MetricsChart } from './MetricsChart'
import { MonitoringHeader } from './MonitoringHeader'
import {
  QueueAlertBanner,
  QueueDepthChartSection,
  QueueSummaryCards,
} from './MonitoringQueueSection'
import type { ChartSeries } from '../lib/metricsChartOptions'
import styles from './MonitoringPanel.module.css'

const REFRESH_MS = 30_000

// 图表 series 为静态常量，保证引用稳定（MetricsChart 依赖引用相等性决定重建时机）
const CONCURRENCY_SERIES: ChartSeries[] = [
  { key: 'online_workers', label: '在线 Worker', color: '#16a34a' },
  { key: 'active_executions', label: '活跃执行', color: '#2563eb' },
]

// workspace 视图的并发图只画活跃执行（在线 Worker 是 fleet 级指标）。
const ACTIVE_SERIES: ChartSeries[] = [
  { key: 'active_executions', label: '活跃执行', color: '#2563eb' },
]

const TOKEN_SERIES: ChartSeries[] = [
  { key: 'input_tokens', label: '输入', color: '#2563eb' },
  { key: 'output_tokens', label: '输出', color: '#7c3aed' },
  { key: 'cache_read_tokens', label: '缓存读', color: '#0891b2' },
]

export function MonitoringPanel({ workspaceId }: { workspaceId?: string }) {
  const [granularity, setGranularity] = useState<OpsGranularity>('6h')
  const [workerId, setWorkerId] = useState('')

  // Worker 列表拉取失败不阻塞监控数据，仅不提供过滤选项（error 不消费）。
  const { data: workerList } = useQuery({
    queryKey: queryKeys.agentWorkers(),
    queryFn: listAgentWorkers,
  })
  const workers = workerList ?? []

  // workspace 与 worker 两种过滤不可叠加（采样行各属其类）；ws 视图隐藏过滤器。
  // 与 QueueDepthChartSection 同参数时共享同一 queryKey，自动合并为一次请求。
  const { data, isPending, error } = useOpsMetrics(
    {
      granularity,
      ...(workerId && !workspaceId ? { worker_id: workerId } : {}),
      ...(workspaceId ? { workspace_id: workspaceId } : {}),
    },
    REFRESH_MS
  )

  const formatTime = useMemo(
    () => makeTimeFormatter(granularity),
    [granularity]
  )
  // 补齐成固定时间窗：切换 Worker 或数据稀疏时图表 X 轴范围不变。
  const buckets = useMemo(
    () => (data ? fillWindowBuckets(data.buckets, granularity) : []),
    [data, granularity]
  )
  // 窗口峰值仍从当前窗口的 buckets 推导；卡片主值统一走后端 summary（分钟级
  // 样本 + node_runs 现算），不随窗口粒度切换而变化。
  const latest = lastNonNullBucket(buckets)
  const summary = data?.summary
  const hourlyTokens = summary?.recent_hour_tokens
  const hourlyRuns = summary?.recent_hour_runs

  if (error) {
    return <p className={styles.error}>监控数据加载失败：{error.message}</p>
  }

  return (
    <section className={styles.panel} aria-label="运维监控">
      <MonitoringHeader
        workspaceId={workspaceId}
        granularity={granularity}
        onGranularityChange={setGranularity}
        workerId={workerId}
        onWorkerChange={setWorkerId}
        workers={workers}
      />

      <QueueAlertBanner summary={summary} />

      <div className={styles.summaryGrid}>
        {!workspaceId && (
          <div className={styles.metric}>
            <div className={styles.metricLabel}>当前在线 Worker</div>
            <div
              className={styles.metricValue}
              data-testid="online-workers-summary"
            >
              {fmt(summary?.online_workers)}
            </div>
            <div className={styles.metricMeta}>
              窗口峰值 {fmt(latest?.online_workers_max)}
            </div>
          </div>
        )}
        <div className={styles.metric}>
          <div className={styles.metricLabel}>当前活跃执行</div>
          <div
            className={styles.metricValue}
            data-testid="active-executions-summary"
          >
            {fmt(summary?.active_executions)}
          </div>
          <div className={styles.metricMeta}>
            窗口峰值 {fmt(latest?.active_executions_max)}
          </div>
        </div>
        <div className={styles.metric}>
          <div className={styles.metricLabel}>近 1 小时 Token 总量</div>
          <div
            className={styles.metricValue}
            data-testid="hourly-tokens-summary"
          >
            {fmt(hourlyTokens?.total_tokens)}
          </div>
          <div className={styles.metricMeta}>
            输入 {fmt(hourlyTokens?.input_tokens)} · 输出{' '}
            {fmt(hourlyTokens?.output_tokens)} · 缓存读{' '}
            {fmt(hourlyTokens?.cache_read_tokens)}
          </div>
        </div>
        <div className={styles.metric}>
          <div className={styles.metricLabel}>近 1 小时 Agent Runs</div>
          <div className={styles.metricValue} data-testid="hourly-runs-summary">
            完成 {fmt(hourlyRuns?.completed)}
          </div>
          <div className={styles.metricMeta}>
            失败 {fmt(hourlyRuns?.failed)} · p50{' '}
            {fmtDuration(hourlyRuns?.duration_p50_seconds)} · p95{' '}
            {fmtDuration(hourlyRuns?.duration_p95_seconds)}
          </div>
        </div>
        <QueueSummaryCards queue={summary?.queue} />
      </div>

      <QueueDepthChartSection
        granularity={granularity}
        formatTime={formatTime}
        workspaceId={workspaceId}
      />

      <div className={styles.chartSection}>
        <h3>
          {workspaceId ? '执行并发' : 'Worker 与执行并发'}
          {isPending ? '（加载中…）' : ''}
        </h3>
        <MetricsChart
          buckets={buckets}
          ariaLabel="在线 Worker 与活跃执行趋势"
          formatTime={formatTime}
          series={workspaceId ? ACTIVE_SERIES : CONCURRENCY_SERIES}
        />
      </div>

      <div className={styles.chartSection}>
        <h3>Token 吞吐量</h3>
        <MetricsChart
          buckets={buckets}
          ariaLabel="Token 吞吐量趋势"
          formatTime={formatTime}
          series={TOKEN_SERIES}
        />
      </div>
    </section>
  )
}
