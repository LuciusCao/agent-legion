import { useQuery } from '@tanstack/react-query'
import { fetchJobTokenUsage } from '../../api/jobApi'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { toErrorMessage } from '../../lib/queryError'
import styles from './TokenUsageJobPanel.module.css'

interface Props {
  jobId: string
}

function fmt(value: number | null | undefined) {
  return typeof value === 'number' ? value.toLocaleString('zh-CN') : '-'
}

function money(currency: string, value: number | null | undefined) {
  if (typeof value !== 'number') return '-'
  const symbol = currency === 'CNY' ? '¥' : currency
  return `${symbol} ${value.toFixed(4)}`
}

export function TokenUsageJobPanel({ jobId }: Props) {
  // key 含 jobId：切换 job 自动回到 pending 态（同原 resetOnRun）。
  const query = useQuery({
    queryKey: extraQueryKeys.jobTokenUsage(jobId),
    queryFn: () => fetchJobTokenUsage(jobId),
  })
  const data = query.data ?? null
  const loading = query.isLoading
  const error = toErrorMessage(query.error)

  if (loading) return <p className={styles.loading}>加载中…</p>
  if (error) return <p className={styles.error}>加载失败：{error}</p>
  if (!data) return <p className={styles.loading}>加载中…</p>

  const total = data.total
  const totalRuns = data.runs_with_usage + data.runs_without_usage
  const coverage = totalRuns
    ? Math.round((data.runs_with_usage / totalRuns) * 100)
    : 0

  return (
    <div className={styles.panel}>
      <div className={styles.summaryGrid}>
        <div className={styles.metric}>
          <div className={styles.metricLabel}>总 Token</div>
          <div className={styles.metricValue}>{fmt(total.total_tokens)}</div>
          <div className={styles.metricMeta}>
            输入 {fmt(total.input_tokens)} / 输出 {fmt(total.output_tokens)} /
            缓存 {fmt(total.cache_read_tokens)}
          </div>
        </div>
        <div className={styles.metric}>
          <div className={styles.metricLabel}>总成本</div>
          <div className={styles.metricValue}>
            {money(data.currency, total.cost?.total)}
          </div>
          <div className={styles.metricMeta}>
            {total.pricing_missing_models?.length
              ? `缺少定价：${total.pricing_missing_models.join('、')}`
              : '按配置单价计算'}
          </div>
        </div>
        <div className={styles.metric}>
          <div className={styles.metricLabel}>覆盖率</div>
          <div className={styles.metricValue}>{coverage}%</div>
          <div className={styles.metricMeta}>
            {data.runs_without_usage} 个 run 无 token 数据
          </div>
        </div>
        <div className={styles.metric}>
          <div className={styles.metricLabel}>Run 数量</div>
          <div className={styles.metricValue}>{totalRuns}</div>
          <div className={styles.metricMeta}>
            {data.runs_with_usage} 个 run 有 usage
          </div>
        </div>
      </div>

      <div className={styles.tableWrap}>
        <table aria-label="Job Token 使用明细">
          <thead>
            <tr>
              <th>Run ID</th>
              <th>节点</th>
              <th>状态</th>
              <th>Provider / Model</th>
              <th>Total Tokens</th>
              <th>Total Cost</th>
            </tr>
          </thead>
          <tbody>
            {data.runs.map((run) => (
              <tr key={run.run_id}>
                <td>{run.run_id}</td>
                <td>{run.node_key || '-'}</td>
                <td>{run.status}</td>
                <td>
                  {run.usage
                    ? `${run.usage.provider || '未知'} / ${run.usage.model || '未知'}`
                    : run.reason || '无数据'}
                </td>
                <td>{fmt(run.usage?.total_tokens)}</td>
                <td className={styles.money}>
                  {run.usage
                    ? money(data.currency, run.usage.cost?.total)
                    : '-'}
                </td>
              </tr>
            ))}
            {data.runs.length === 0 && (
              <tr>
                <td colSpan={6} className={styles.emptyCell}>
                  暂无 token 统计
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
