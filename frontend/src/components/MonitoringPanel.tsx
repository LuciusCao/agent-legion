import { useMemo, useState } from 'react'
import {
  FormControl,
  MenuItem,
  Select,
  ToggleButton,
  ToggleButtonGroup,
} from '@mui/material'
import { fetchOpsMetrics } from '../api/metrics'
import type { OpsGranularity } from '../api/metrics'
import { listAgentWorkers } from '../api/workerTokens'
import { fillWindowBuckets } from '../lib/opsMetricsWindow'
import { lastNonNullBucket } from '../lib/opsMetricsBuckets'
import { MetricsChart } from './MetricsChart'
import type { ChartSeries } from '../lib/metricsChartOptions'
import { useAsync } from '../hooks/useAsync'
import styles from './MonitoringPanel.module.css'

const REFRESH_MS = 30_000

function fmt(value: number | null | undefined) {
  return typeof value === 'number' ? value.toLocaleString('zh-CN') : '-'
}

// 耗时展示：不足 1 分钟按秒，否则「Xm YYs」；无数据（窗口内无完成 run）显示占位符。
function fmtDuration(seconds: number | null | undefined) {
  if (typeof seconds !== 'number') return '-'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return `${minutes}m ${pad(rest)}s`
}

function pad(n: number) {
  return String(n).padStart(2, '0')
}

function makeTimeFormatter(granularity: OpsGranularity) {
  return (iso: string) => {
    const d = new Date(iso)
    // 30d 的 4 小时桶需要「日期 + 小时」才能区分同一天内的多个桶
    if (granularity === '30d')
      return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:00`
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`
  }
}

// 图表 series 为静态常量，保证引用稳定（MetricsChart 依赖引用相等性决定重建时机）
const CONCURRENCY_SERIES: ChartSeries[] = [
  { key: 'online_workers', label: '在线 Worker', color: '#16a34a' },
  { key: 'active_executions', label: '活跃执行', color: '#2563eb' },
]

const TOKEN_SERIES: ChartSeries[] = [
  { key: 'input_tokens', label: '输入', color: '#2563eb' },
  { key: 'output_tokens', label: '输出', color: '#7c3aed' },
  { key: 'cache_read_tokens', label: '缓存读', color: '#0891b2' },
]

export function MonitoringPanel() {
  const [granularity, setGranularity] = useState<OpsGranularity>('6h')
  const [workerId, setWorkerId] = useState('')

  // Worker 列表拉取失败不阻塞监控数据，仅不提供过滤选项（error 不消费）。
  const { data: workerList } = useAsync(() => listAgentWorkers(), [])
  const workers = workerList ?? []

  const { data, loading, error } = useAsync(
    () =>
      fetchOpsMetrics({
        granularity,
        ...(workerId ? { worker_id: workerId } : {}),
      }),
    [granularity, workerId],
    { refetchInterval: REFRESH_MS }
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
    return <p className={styles.error}>监控数据加载失败：{error}</p>
  }

  return (
    <section className={styles.panel} aria-label="运维监控">
      <header className={styles.header}>
        <div className={styles.titleGroup}>
          <h2>运维监控</h2>
          <p>在线 Worker、执行并发与 token 吞吐趋势，每 30 秒自动刷新。</p>
        </div>
        <div className={styles.controls}>
          <FormControl size="small">
            <Select
              value={workerId}
              onChange={(e) => setWorkerId(e.target.value)}
              displayEmpty
              inputProps={{ 'aria-label': '选择 Worker' }}
            >
              <MenuItem value="">全部 Worker</MenuItem>
              {workers.map((w) => (
                <MenuItem key={w.worker_id} value={w.worker_id}>
                  {w.name || w.worker_id}
                  {w.online ? '（在线）' : '（离线）'}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={granularity}
            onChange={(_e, value: OpsGranularity | null) => {
              if (value) setGranularity(value)
            }}
            aria-label="时间粒度"
          >
            <ToggleButton value="6h">近 6 小时</ToggleButton>
            <ToggleButton value="24h">近 24 小时</ToggleButton>
            <ToggleButton value="30d">近 30 天</ToggleButton>
          </ToggleButtonGroup>
        </div>
      </header>

      <div className={styles.summaryGrid}>
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
          <div className={styles.metricLabel}>近 1 小时 Runs</div>
          <div className={styles.metricValue} data-testid="hourly-runs-summary">
            完成 {fmt(hourlyRuns?.completed)}
          </div>
          <div className={styles.metricMeta}>
            失败 {fmt(hourlyRuns?.failed)} · p50{' '}
            {fmtDuration(hourlyRuns?.duration_p50_seconds)} · p95{' '}
            {fmtDuration(hourlyRuns?.duration_p95_seconds)}
          </div>
        </div>
      </div>

      <div className={styles.chartSection}>
        <h3>Worker 与执行并发{loading && !data ? '（加载中…）' : ''}</h3>
        <MetricsChart
          buckets={buckets}
          ariaLabel="在线 Worker 与活跃执行趋势"
          formatTime={formatTime}
          series={CONCURRENCY_SERIES}
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
