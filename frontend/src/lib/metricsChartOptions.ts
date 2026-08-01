import uPlot from 'uplot'
import type { WindowBucket } from './opsMetricsBuckets'

export interface ChartSeries {
  key: keyof Omit<WindowBucket, 'bucket_start'>
  label: string
  color: string
}

export const CHART_HEIGHT = 220
const GRID_LINE = '#eef0f3'
const AXIS_LINE = '#e5e7eb'
const TICK_LABEL = '#8a8f98'

function fmtTick(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 10_000) return `${Math.round(value / 1_000)}k`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`
  return String(Math.round(value))
}

function hexToRgba(hex: string, alpha: number) {
  const n = parseInt(hex.slice(1), 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
}

// uPlot 数据：x 为 unix 秒；y 里 null 会让折线断开（采样缺失时段不跌零）。
export function buildData(
  buckets: WindowBucket[],
  series: ChartSeries[]
): uPlot.AlignedData {
  const x = buckets.map((b) => new Date(b.bucket_start).getTime() / 1000)
  const ys = series.map((s) => buckets.map((b) => b[s.key]))
  return [x, ...ys] as uPlot.AlignedData
}

export function buildOptions(
  width: number,
  series: ChartSeries[],
  area: boolean,
  formatTime: (iso: string) => string,
  onCursor: (next: { idx: number; left: number } | null) => void
): uPlot.Options {
  return {
    width,
    height: CHART_HEIGHT,
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
