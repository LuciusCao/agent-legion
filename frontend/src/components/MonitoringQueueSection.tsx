import { useMemo } from 'react'
import type { OpsGranularity, OpsMetricsResponse } from '../api/metrics'
import { useOpsMetrics } from '../hooks/useOpsMetrics'
import { fillWindowBuckets } from '../lib/opsMetricsWindow'
import { MetricsChart } from './MetricsChart'
import type { ChartSeries } from '../lib/metricsChartOptions'
import panelStyles from './MonitoringPanel.module.css'
import styles from './MonitoringQueueSection.module.css'

type QueueSummary = OpsMetricsResponse['summary']['queue']

// 队列深度为全局指标（不随 Worker 过滤器过滤），用琥珀色与并发图的蓝/绿区分。
const QUEUE_SERIES: ChartSeries[] = [
  { key: 'queued', label: '排队深度', color: '#d97706' },
]

// 与后端 claim_scan 的 skip 原因计数键一一对应。
const REASON_LABELS: Record<string, string> = {
  capability_or_model_mismatch: 'model/capability 不匹配',
  runtime_mismatch: 'runtime 不匹配',
  labels_mismatch: 'labels 不匹配',
  workspace_not_allowed: 'workspace 不在 worker 范围',
  workspace_paused: 'workspace 已暂停',
  job_paused: 'job 已暂停',
  job_terminal: 'job 已终止',
  job_missing: 'job 已删除',
  lock_raced: '行锁竞争',
  capacity_raced: '容量竞争',
  node_not_pending: '节点状态已变更',
}

function formatReasons(reasons: Record<string, number>) {
  return Object.entries(reasons)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([key, count]) => `${REASON_LABELS[key] ?? key} ×${count}`)
    .join('、')
}

function formatAge(iso: string | null) {
  if (!iso) return '-'
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 120) return `${Math.round(seconds)}s`
  const minutes = seconds / 60
  if (minutes < 60) return `${Math.round(minutes)}m`
  const hours = minutes / 60
  if (hours < 48) return `${hours.toFixed(1)}h`
  return `${Math.round(hours / 24)}d`
}

function ageSeconds(iso: string | null) {
  return iso ? Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000) : 0
}

/** 队列告警：blocked（有货无人可领）红色，stalled（无人来领）黄色；健康时不渲染。 */
export function QueueAlertBanner({
  summary,
}: {
  summary: OpsMetricsResponse['summary'] | undefined
}) {
  const alert = summary?.queue_alert
  if (!alert) return null
  const count = (summary?.queue?.queued ?? 0).toLocaleString('zh-CN')
  if (alert.kind === 'blocked') {
    const reasons = formatReasons(alert.reasons)
    return (
      <div className={`${styles.alert} ${styles.alertBlocked}`} role="alert">
        <b>队列堵塞</b>：{count} 条请求排队中，最近的领取全部落空
        {reasons && `——${reasons}`}
      </div>
    )
  }
  return (
    <div className={`${styles.alert} ${styles.alertStalled}`} role="alert">
      <b>队列停滞</b>：{count} 条请求排队中，worker
      空闲但无人领取——可能原因：worker 领取开关关闭 / workspace 已暂停
    </div>
  )
}

/** 队列深度与 sweeper 处置两张摘要卡（渲染进监控页 summaryGrid）。 */
export function QueueSummaryCards({
  queue,
}: {
  queue: QueueSummary | undefined
}) {
  const oldest = queue?.oldest_queued_at ?? null
  const stale = ageSeconds(oldest) > 3600
  return (
    <>
      <div className={panelStyles.metric}>
        <div className={panelStyles.metricLabel}>当前队列深度</div>
        <div
          className={panelStyles.metricValue}
          data-testid="queue-depth-summary"
        >
          {typeof queue?.queued === 'number'
            ? queue.queued.toLocaleString('zh-CN')
            : '-'}
        </div>
        <div className={panelStyles.metricMeta}>
          队首最老{' '}
          <span className={stale ? styles.metricStale : undefined}>
            {formatAge(oldest)}
          </span>
        </div>
      </div>
      <div className={panelStyles.metric}>
        <div className={panelStyles.metricLabel}>近 1 小时 sweeper 处置</div>
        <div
          className={panelStyles.metricValue}
          data-testid="queue-sweeper-summary"
        >
          {typeof queue?.recent_hour_unclaimable_failed === 'number'
            ? queue.recent_hour_unclaimable_failed.toLocaleString('zh-CN')
            : '-'}
        </div>
        <div className={panelStyles.metricMeta}>不可 claim 请求自动 fail</div>
      </div>
    </>
  )
}

/** 队列深度趋势图；自取自同粒度请求，可挂 workspace 作用域（v23）。 */
export function QueueDepthChartSection({
  granularity,
  formatTime,
  workspaceId,
}: {
  granularity: OpsGranularity
  formatTime: (iso: string) => string
  workspaceId?: string
}) {
  // 与监控面板同频（30s）刷新；全局视图不带过滤，ws 视图按 workspace 过滤。
  // 参数与 MonitoringPanel 主查询一致时共享 queryKey，合并为一次请求。
  const { data, isPending } = useOpsMetrics(
    { granularity, ...(workspaceId ? { workspace_id: workspaceId } : {}) },
    30_000
  )
  const buckets = useMemo(
    () => (data ? fillWindowBuckets(data.buckets, granularity) : []),
    [data, granularity]
  )
  return (
    <div className={panelStyles.chartSection}>
      <h3>
        队列深度{workspaceId ? '' : '（全局）'}
        {isPending ? '（加载中…）' : ''}
      </h3>
      <MetricsChart
        buckets={buckets}
        ariaLabel="Agent 执行队列深度趋势"
        formatTime={formatTime}
        series={QUEUE_SERIES}
      />
    </div>
  )
}
