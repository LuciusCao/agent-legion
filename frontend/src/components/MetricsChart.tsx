import { useEffect, useRef, useState } from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import type { WindowBucket } from '../lib/opsMetricsBuckets'
import type { ChartSeries } from '../lib/metricsChartOptions'
import {
  CHART_HEIGHT,
  buildData,
  buildOptions,
} from '../lib/metricsChartOptions'
import styles from './MetricsChart.module.css'

interface MetricsChartProps {
  buckets: WindowBucket[]
  series: ChartSeries[]
  area?: boolean
  ariaLabel: string
  formatTime: (iso: string) => string
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
  const hostRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<uPlot | null>(null)
  const bucketsRef = useRef(buckets)
  const [hover, setHover] = useState<{ idx: number; left: number } | null>(null)

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
      if (width > 0) chart.setSize({ width, height: CHART_HEIGHT })
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
                <span
                  className={styles.swatch}
                  style={{ background: s.color }}
                />
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
