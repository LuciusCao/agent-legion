import { useEffect, useRef, useState } from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import type { WindowBucket } from '../lib/opsMetricsWindow'
import styles from './MetricsChart.module.css'

export interface ChartSeries {
  key: keyof Omit<WindowBucket, 'bucket_start'>
  label: string
  color: string
}

interface MetricsChartProps {
  buckets: WindowBucket[]
  series: ChartSeries[]
  area?: boolean
  ariaLabel: string
  formatTime: (iso: string) => string
}

const H = 220
const GRID_LINE = '#eef0f3'
const AXIS_LINE = '#e5e7eb'
const TICK_LABEL = '#8a8f98'

function fmtTick(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 10_000) return `${Math.round(value / 1_000)}k`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`
  return String(Math.round(value))
}

function fmtFull(value: number) {
  return value.toLocaleString('zh-CN')
}

function hexToRgba(hex: string, alpha: number) {
  const n = parseInt(hex.slice(1), 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
}

// uPlot 数据：x 为 unix 秒；y 里 null 会让折线断开（采样缺失时段不跌零）。
function buildData(
  buckets: WindowBucket[],
  series: ChartSeries[]
): uPlot.AlignedData {
  const x = buckets.map((b) => new Date(b.bucket_start).getTime() / 1000)
  const ys = series.map((s) => buckets.map((b) => b[s.key]))
  return [x, ...ys] as uPlot.AlignedData
}

function buildOptions(
  width: number,
  series: ChartSeries[],
  area: boolean,
  formatTime: (iso: string) => string,
  onCursor: (next: { idx: number; left: number } | null) => void
): uPlot.Options {
  return {
    width,
    height: H,
    // 右/上留 8px 内边距，避免末端的点和峰值的线贴画布边被裁（CSS px）
    padding: [8, 8, 0, 0],
    legend: { show: false },
    cursor: { drag: { x: false, y: false } },
    scales: {
      x: { time: false },
      // Y 轴固定从 0 开始，顶部留 10% 余量避免峰值线贴边被裁；
      // 全 null（无数据）时退化为 0..1 空坐标系。
      y: {
        range: (_u, _min, max) =>
          max == null || max <= 0 ? [0, 1] : [0, max * 1.1],
      },
    },
    axes: [
      {
        stroke: TICK_LABEL,
        font: '10px sans-serif',
        grid: { show: false },
        ticks: { show: false },
        border: { show: true, stroke: AXIS_LINE, width: 1 },
        values: (_u, splits) =>
          splits.map((ts) => formatTime(new Date(ts * 1000).toISOString())),
      },
      {
        stroke: TICK_LABEL,
        font: '10px sans-serif',
        grid: { stroke: GRID_LINE, width: 1 },
        ticks: { show: false },
        values: (_u, splits) => splits.map((v) => fmtTick(v)),
      },
    ],
    series: [
      {},
      ...series.map((s, i) => ({
        stroke: s.color,
        width: 2,
        fill: area && i === 0 ? hexToRgba(s.color, 0.15) : undefined,
        // step-after：值持续整个采样时段，与后端分钟级采样语义一致
        // stepped 是 uPlot 内置 path builder，类型上标为可选
        paths: uPlot.paths.stepped!({ align: 1 }),
        points: { show: false },
      })),
    ],
    hooks: {
      setCursor: [
        (u) => {
          const { left, idx } = u.cursor
          if (left == null || left < 0 || idx == null) {
            onCursor(null)
            return
          }
          onCursor({ idx, left: left + u.over.offsetLeft })
        },
      ],
    },
  }
}

export function MetricsChart({
  buckets,
  series,
  area = false,
  ariaLabel,
  formatTime,
}: MetricsChartProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<uPlot | null>(null)
  const bucketsRef = useRef(buckets)
  const [hover, setHover] = useState<{ idx: number; left: number } | null>(
    null
  )

  const hasData = buckets.length > 0

  // 图表只在 series / area / formatTime 变化时重建；数据经 setData 增量更新。
  // jsdom 下容器 clientWidth 为 0，跳过初始化（uPlot 需要 canvas，测试环境不可用）。
  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const create = (width: number) => {
      if (chartRef.current || width <= 0) return
      chartRef.current = new uPlot(
        buildOptions(width, series, area, formatTime, setHover),
        buildData(bucketsRef.current, series),
        host
      )
    }
    create(host.clientWidth)
    const ro = new ResizeObserver(() => {
      const width = host.clientWidth
      const chart = chartRef.current
      if (!chart) {
        create(width)
        return
      }
      if (width > 0) chart.setSize({ width, height: H })
    })
    ro.observe(host)
    return () => {
      ro.disconnect()
      chartRef.current?.destroy()
      chartRef.current = null
    }
  }, [series, area, formatTime, hasData])

  useEffect(() => {
    bucketsRef.current = buckets
    chartRef.current?.setData(buildData(buckets, series))
  }, [buckets, series])

  if (!hasData) {
    return <div className={styles.empty}>暂无数据</div>
  }

  const hoverBucket = hover ? buckets[hover.idx] : null

  return (
    <div className={styles.chart}>
      <div ref={hostRef} role="img" aria-label={ariaLabel} />
      {hover && hoverBucket && (
        <div className={styles.tooltip} style={{ left: hover.left }}>
          <span className={styles.tooltipTime}>
            {formatTime(hoverBucket.bucket_start)}
          </span>
          {series.map((s) => {
            const value = hoverBucket[s.key]
            if (value === null) return null
            return (
              <span key={s.key} className={styles.tooltipRow}>
                <span className={styles.swatch} style={{ background: s.color }} />
                {s.label} {fmtFull(value)}
              </span>
            )
          })}
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
