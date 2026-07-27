import { useEffect, useMemo, useState } from 'react'
import {
  FormControl,
  MenuItem,
  Select,
  ToggleButton,
  ToggleButtonGroup,
} from '@mui/material'
import { fetchOpsMetrics } from '../api/metrics'
import type {
  MetricBucket,
  OpsGranularity,
  OpsMetricsResponse,
} from '../api/metrics'
import { listAgentWorkers } from '../api/workerTokens'
import type { AgentWorkerSummary } from '../api/workerTokens'
import { MetricsChart } from './MetricsChart'
import styles from './MonitoringPanel.module.css'

const REFRESH_MS = 30_000
const HOUR_MS = 3_600_000

const WINDOWS: Record<OpsGranularity, { hours?: number; days?: number }> = {
  minute: { hours: 6 },
  hour: { hours: 24 },
  day: { days: 7 },
}

function fmt(value: number | null | undefined) {
  return typeof value === 'number' ? value.toLocaleString('zh-CN') : '-'
}

function pad(n: number) {
  return String(n).padStart(2, '0')
}

function makeTimeFormatter(granularity: OpsGranularity) {
  return (iso: string) => {
    const d = new Date(iso)
    if (granularity === 'day')
      return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
    if (granularity === 'hour')
      return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:00`
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`
  }
}

function latestBucket(data: OpsMetricsResponse | null): MetricBucket | null {
  return data?.buckets.length ? data.buckets[data.buckets.length - 1] : null
}

function sumRecentHourTokens(data: OpsMetricsResponse | null): number | null {
  if (!data?.buckets.length) return null
  const cutoff = Date.now() - HOUR_MS
  const recent = data.buckets.filter(
    (b) => new Date(b.bucket_start).getTime() >= cutoff
  )
  const source = recent.length
    ? recent
    : [data.buckets[data.buckets.length - 1]]
  return source.reduce((sum, b) => sum + b.total_tokens, 0)
}

export function MonitoringPanel() {
  const [granularity, setGranularity] = useState<OpsGranularity>('minute')
  const [workerId, setWorkerId] = useState('')
  const [workers, setWorkers] = useState<AgentWorkerSummary[]>([])
  const [data, setData] = useState<OpsMetricsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let stale = false
    listAgentWorkers()
      .then((list) => {
        if (!stale) setWorkers(list)
      })
      .catch(() => {
        // Worker 列表拉取失败不阻塞监控数据，仅不提供过滤选项。
      })
    return () => {
      stale = true
    }
  }, [])

  useEffect(() => {
    let stale = false
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loading state tied to fetch lifecycle
    setLoading(true)
    setError(null)
    const load = () => {
      fetchOpsMetrics({
        granularity,
        ...WINDOWS[granularity],
        ...(workerId ? { worker_id: workerId } : {}),
      })
        .then((next) => {
          if (!stale) setData(next)
        })
        .catch((err) => {
          if (!stale) setError(err instanceof Error ? err.message : String(err))
        })
        .finally(() => {
          if (!stale) setLoading(false)
        })
    }
    load()
    const timer = setInterval(load, REFRESH_MS)
    return () => {
      stale = true
      clearInterval(timer)
    }
  }, [granularity, workerId])

  const formatTime = useMemo(
    () => makeTimeFormatter(granularity),
    [granularity]
  )
  const latest = latestBucket(data)
  const hourlyTokens = sumRecentHourTokens(data)

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
            <ToggleButton value="minute">分钟 · 近 6 小时</ToggleButton>
            <ToggleButton value="hour">小时 · 近 24 小时</ToggleButton>
            <ToggleButton value="day">天 · 近 7 天</ToggleButton>
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
            {fmt(latest?.online_workers)}
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
            {fmt(latest?.active_executions)}
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
            {fmt(hourlyTokens)}
          </div>
          <div className={styles.metricMeta}>输入 / 输出 / 缓存读合并统计</div>
        </div>
      </div>

      <div className={styles.chartSection}>
        <h3>Worker 与执行并发{loading && !data ? '（加载中…）' : ''}</h3>
        <MetricsChart
          buckets={data?.buckets ?? []}
          ariaLabel="在线 Worker 与活跃执行趋势"
          formatTime={formatTime}
          series={[
            { key: 'online_workers', label: '在线 Worker', color: '#16a34a' },
            { key: 'active_executions', label: '活跃执行', color: '#2563eb' },
          ]}
        />
      </div>

      <div className={styles.chartSection}>
        <h3>Token 吞吐量</h3>
        <MetricsChart
          buckets={data?.buckets ?? []}
          ariaLabel="Token 吞吐量趋势"
          formatTime={formatTime}
          area
          series={[
            { key: 'total_tokens', label: '总 Token', color: '#7c3aed' },
          ]}
        />
      </div>
    </section>
  )
}
