import { useState } from 'react'
import { fetchRunTokenUsage } from '../api/jobApi'
import { useAsync } from '../hooks/useAsync'
import type { NodeRun } from '../types/jobTypes'
import type { RunUsage } from '../types/tokenUsageTypes'
import { MaterialIcon } from './MaterialIcon'
import styles from './TokenUsageRunDetail.module.css'

type TokenUsageRunDetailProps = { jobId: string; run: NodeRun }

function formatCost(value: number, currency: string): string {
  if (value === 0) return `${currency} 0.0000`
  if (value < 0.0001) return `${currency} <0.0001`
  return `${currency} ${value.toFixed(4)}`
}

export function TokenUsageRunDetail({ jobId, run }: TokenUsageRunDetailProps) {
  const { data: response, loading } = useAsync(
    () => fetchRunTokenUsage(jobId, run.id),
    [jobId, run.id, run.status]
  )
  const [expanded, setExpanded] = useState(false)

  const usage = response?.usage ?? null

  const compactText = (() => {
    if (loading) return 'Token 用量...'
    if (!response || response.usage === null) return '无 token 数据'
    const total = usage?.total_tokens ?? 0
    const costTotal = usage?.cost?.total
    const currency = usage?.cost?.currency
    const costText =
      typeof costTotal === 'number' && currency
        ? formatCost(costTotal, currency)
        : undefined
    return `Token: ${total.toLocaleString('zh-CN')}${costText ? ` · ${costText}` : ''}`
  })()

  return (
    <div className={styles.container}>
      <button
        type="button"
        className={styles.compactBtn}
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        aria-label="Token 用量"
      >
        <MaterialIcon
          name="data_object"
          className={styles.icon}
          sx={{ fontSize: 14 }}
        />
        <span>{compactText}</span>
        <MaterialIcon
          name={expanded ? 'expand_less' : 'expand_more'}
          className={styles.icon}
          sx={{ fontSize: 14 }}
        />
      </button>
      {expanded && (
        <div className={styles.detailBox}>
          {!response || response.usage === null ? (
            <p className={styles.empty}>
              {response?.reason ?? '无 token 数据'}
            </p>
          ) : (
            <TokenUsageBreakdown usage={usage} />
          )}
        </div>
      )}
    </div>
  )
}

function TokenUsageBreakdown({ usage }: { usage: RunUsage | null }) {
  if (!usage) return <p className={styles.empty}>无 token 数据</p>

  const {
    provider,
    model,
    skill_version,
    message_count,
    input_tokens,
    output_tokens,
    cache_read_tokens,
    total_tokens,
    cost,
    is_complete,
  } = usage

  return (
    <div className={styles.breakdown}>
      {!is_complete && (
        <div className={styles.warningRow}>
          <MaterialIcon
            name="warning"
            className={styles.warningIcon}
            sx={{ fontSize: 14 }}
          />
          部分 token 数据可能不完整
        </div>
      )}
      <div className={styles.row}>
        <span className={styles.label}>Provider / Model</span>
        <span className={styles.value}>
          {provider || '未知'} / {model || '未知'}
        </span>
      </div>
      {skill_version && (
        <div className={styles.row}>
          <span className={styles.label}>Skill 版本</span>
          <span className={styles.value}>{skill_version}</span>
        </div>
      )}
      <div className={styles.row}>
        <span className={styles.label}>消息数</span>
        <span className={styles.value}>{message_count}</span>
      </div>
      <div className={styles.sectionTitle}>Token 明细</div>
      <div className={styles.grid}>
        <TokenMetric label="Input" value={input_tokens} />
        <TokenMetric label="Output" value={output_tokens} />
        <TokenMetric label="Cache read" value={cache_read_tokens} />
        <TokenMetric label="Total" value={total_tokens} highlight />
      </div>
      <div className={styles.sectionTitle}>费用明细</div>
      {cost ? (
        <div className={styles.grid}>
          <CostMetric
            label="Input"
            value={cost.input}
            currency={cost.currency}
          />
          <CostMetric
            label="Output"
            value={cost.output}
            currency={cost.currency}
          />
          <CostMetric
            label="Cache read"
            value={cost.cache_read}
            currency={cost.currency}
          />
          <CostMetric
            label="Total"
            value={cost.total}
            currency={cost.currency}
            highlight
          />
        </div>
      ) : (
        <p className={styles.empty}>未配置价格</p>
      )}
      {usage.pricing_missing && (
        <div className={styles.pricingMissing}>缺少定价配置</div>
      )}
    </div>
  )
}

function TokenMetric({
  label,
  value,
  highlight,
}: {
  label: string
  value: number
  highlight?: boolean
}) {
  return (
    <div
      className={`${styles.metric} ${highlight ? styles.metricHighlight : ''}`}
    >
      <span className={styles.metricLabel}>{label}</span>
      <span className={styles.metricValue}>
        {value.toLocaleString('zh-CN')}
      </span>
    </div>
  )
}

function CostMetric({
  label,
  value,
  currency,
  highlight,
}: {
  label: string
  value: number | null | undefined
  currency: string
  highlight?: boolean
}) {
  return (
    <div
      className={`${styles.metric} ${highlight ? styles.metricHighlight : ''}`}
    >
      <span className={styles.metricLabel}>{label}</span>
      <span className={styles.metricValue}>
        {typeof value === 'number' ? formatCost(value, currency) : '-'}
      </span>
    </div>
  )
}
