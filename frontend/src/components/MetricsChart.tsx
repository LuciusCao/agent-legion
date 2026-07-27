import { useMemo, useState } from 'react'
import type { MetricBucket } from '../api/metrics'
import styles from './MetricsChart.module.css'

export interface ChartSeries {
  key: 'online_workers' | 'active_executions' | 'total_tokens'
  label: string
  color: string
}

interface MetricsChartProps {
  buckets: MetricBucket[]
  series: ChartSeries[]
  area?: boolean
  ariaLabel: string
  formatTime: (iso: string) => string
}

const W = 640
const H = 220
const PAD = { left: 48, right: 12, top: 12, bottom: 28 }
const INNER_W = W - PAD.left - PAD.right
const INNER_H = H - PAD.top - PAD.bottom
const Y_TICKS = 4
const X_LABELS = 5

function fmtTick(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 10_000) return `${Math.round(value / 1_000)}k`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`
  return String(Math.round(value))
}

function fmtFull(value: number) {
  return value.toLocaleString('zh-CN')
}

export function MetricsChart({
  buckets,
  series,
  area = false,
  ariaLabel,
  formatTime,
}: MetricsChartProps) {
  const [hover, setHover] = useState<{ index: number; left: number } | null>(
    null
  )

  const max = useMemo(() => {
    let m = 0
    for (const bucket of buckets) {
      for (const s of series) m = Math.max(m, bucket[s.key])
    }
    return m || 1
  }, [buckets, series])

  if (buckets.length === 0) {
    return <div className={styles.empty}>暂无数据</div>
  }

  const stepX = buckets.length > 1 ? INNER_W / (buckets.length - 1) : 0
  const xAt = (i: number) => PAD.left + i * stepX
  const yAt = (v: number) => PAD.top + INNER_H - (v / max) * INNER_H

  const lineFor = (key: ChartSeries['key']) =>
    buckets.map((b, i) => `${xAt(i)},${yAt(b[key])}`).join(' ')

  const areaPath = `M ${xAt(0)},${yAt(0)} L ${buckets
    .map((b, i) => `${xAt(i)},${yAt(b[series[0].key])}`)
    .join(' L ')} L ${xAt(buckets.length - 1)},${yAt(0)} Z`

  const xLabelIndices = Array.from(
    new Set(
      Array.from({ length: X_LABELS }, (_, i) =>
        Math.round((i * (buckets.length - 1)) / (X_LABELS - 1))
      )
    )
  )

  const handleMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const scaleX = W / rect.width
    const x = (e.clientX - rect.left) * scaleX
    const index = Math.min(
      buckets.length - 1,
      Math.max(0, Math.round((x - PAD.left) / stepX))
    )
    setHover({ index, left: e.clientX - rect.left })
  }

  return (
    <div className={styles.chart}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={ariaLabel}
        onMouseMove={handleMove}
        onMouseLeave={() => setHover(null)}
      >
        {Array.from({ length: Y_TICKS + 1 }, (_, i) => {
          const v = (max / Y_TICKS) * i
          const y = yAt(v)
          return (
            <g key={i}>
              <line
                className={i === 0 ? styles.axisLine : styles.gridLine}
                x1={PAD.left}
                x2={W - PAD.right}
                y1={y}
                y2={y}
              />
              <text
                className={styles.tickLabel}
                x={PAD.left - 6}
                y={y + 3}
                textAnchor="end"
              >
                {fmtTick(v)}
              </text>
            </g>
          )
        })}
        {area && (
          <path d={areaPath} fill={series[0].color} fillOpacity={0.15} />
        )}
        {series.map((s) => (
          <polyline
            key={s.key}
            points={lineFor(s.key)}
            fill="none"
            stroke={s.color}
            strokeWidth={2}
            strokeLinejoin="round"
          />
        ))}
        {xLabelIndices.map((i) => (
          <text
            key={i}
            className={styles.tickLabel}
            x={xAt(i)}
            y={H - 8}
            textAnchor="middle"
          >
            {formatTime(buckets[i].bucket_start)}
          </text>
        ))}
        {hover && (
          <g>
            <line
              className={styles.hoverLine}
              x1={xAt(hover.index)}
              x2={xAt(hover.index)}
              y1={PAD.top}
              y2={PAD.top + INNER_H}
            />
            {series.map((s) => (
              <circle
                key={s.key}
                cx={xAt(hover.index)}
                cy={yAt(buckets[hover.index][s.key])}
                r={3}
                fill={s.color}
              />
            ))}
          </g>
        )}
      </svg>
      {hover && (
        <div className={styles.tooltip} style={{ left: hover.left }}>
          <span className={styles.tooltipTime}>
            {formatTime(buckets[hover.index].bucket_start)}
          </span>
          {series.map((s) => (
            <span key={s.key} className={styles.tooltipRow}>
              <span className={styles.swatch} style={{ background: s.color }} />
              {s.label} {fmtFull(buckets[hover.index][s.key])}
            </span>
          ))}
        </div>
      )}
      <div className={styles.legend}>
        {series.map((s) => (
          <span key={s.key} className={styles.legendItem}>
            <span className={styles.swatch} style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
      </div>
    </div>
  )
}
