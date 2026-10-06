import { INTERACTION_TYPE_LABELS } from '../labels'
import type { InteractionStats } from '../types'

/**
 * 前端格式化的唯一归口（#966）：文件大小、数字、日期时间、相对时间统一从
 * 本模块取，不在组件里再写一份。locale 统一 `zh-CN`（中文 UI；既有调用
 * 多数已显式写 zh-CN，少数用浏览器默认的已收敛到这里）。
 */
export const DISPLAY_LOCALE = 'zh-CN'

const BYTE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB'] as const

/**
 * 字节数 → 人类可读大小：1024 进制，单位阶梯 B/KB/MB/GB/TB（超过 TB 仍按
 * TB 计），保留至多 1 位小数且去掉多余的 `.0`（`2 KB`、`1.5 MB`）。
 * 非法值（负数 / NaN / Infinity）显示占位符。
 */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '—'
  if (bytes < 1) return `${bytes} B`
  const exponent = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    BYTE_UNITS.length - 1
  )
  const value = parseFloat((bytes / Math.pow(1024, exponent)).toFixed(1))
  return `${value} ${BYTE_UNITS[exponent]}`
}

/** 千分位数字（zh-CN）；非数字显示占位符（默认 `-`，与监控/用量面板一致）。 */
export function formatNumber(
  value: number | null | undefined,
  placeholder = '-'
): string {
  return typeof value === 'number'
    ? value.toLocaleString(DISPLAY_LOCALE)
    : placeholder
}

export function formatDuration(ms: number): string {
  if (ms <= 0) return '—'
  const sec = Math.floor(ms / 1000)
  const m = Math.floor(sec / 60)
  const s = sec % 60
  if (m >= 60) {
    const h = Math.floor(m / 60)
    return `${h}时${m % 60}分${s}秒`
  }
  return m > 0 ? `${m}分${s}秒` : `${s}秒`
}

/**
 * Render a backend timestamp in the browser's local timezone. Backend
 * timestamps are UTC ISO strings with an explicit offset; legacy SQLite
 * rows can still return offset-less "YYYY-MM-DD HH:MM:SS" strings (also
 * UTC), which browsers would otherwise parse as local time.
 */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const hasOffset = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value.trim())
  const date = new Date(hasOffset ? value : `${value.replace(' ', 'T')}Z`)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString(DISPLAY_LOCALE)
}

export function formatRelativeTime(isoDate: string): string {
  const date = new Date(isoDate)
  const now = new Date()
  const seconds = Math.floor((now.getTime() - date.getTime()) / 1000)
  if (seconds < 60) return '刚刚'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days} 天前`
  return date.toLocaleDateString(DISPLAY_LOCALE)
}

export function durationSeconds(
  start?: string | null,
  end?: string | null
): number | undefined {
  if (!start || !end) return undefined
  const s = new Date(start).getTime()
  const e = new Date(end).getTime()
  if (Number.isNaN(s) || Number.isNaN(e)) return undefined
  const diff = Math.round((e - s) / 1000)
  return diff >= 0 ? diff : 0
}

export function formatInteractionStats(
  stats: Record<string, InteractionStats> | undefined
): string {
  if (!stats) return ''
  const parts: string[] = []
  const order = ['example_practice', 'interaction_summary', 'video_summary']
  for (const type of order) {
    if (stats[type]) {
      const label = INTERACTION_TYPE_LABELS[type] || type
      const { passed, total } = stats[type]
      parts.push(`${label} ${passed}/${total}`)
    }
  }
  for (const [type, { passed, total }] of Object.entries(stats)) {
    if (!order.includes(type)) {
      const label = INTERACTION_TYPE_LABELS[type] || type
      parts.push(`${label} ${passed}/${total}`)
    }
  }
  return parts.join(' ｜ ')
}
