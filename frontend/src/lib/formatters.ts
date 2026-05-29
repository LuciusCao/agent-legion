import { INTERACTION_TYPE_LABELS } from '../labels'
import type { InteractionStats } from '../types'

export function seconds(value: number): string {
  const minutes = Math.floor(value / 60)
  const secs = Math.floor(value % 60)
  return `${minutes}:${secs.toString().padStart(2, '0')}`
}

export function parseTimeSeconds(value: unknown): number {
  if (typeof value === 'number') return value
  if (typeof value !== 'string') return Number.NaN

  const trimmed = value.trim()
  if (!trimmed) return Number.NaN

  const numeric = Number(trimmed)
  if (Number.isFinite(numeric)) return numeric

  const parts = trimmed.replace(',', '.').split(':')
  if (parts.length === 2) {
    const minutes = Number(parts[0])
    const seconds = Number(parts[1])
    if (Number.isFinite(minutes) && Number.isFinite(seconds)) {
      return minutes * 60 + seconds
    }
  }
  if (parts.length === 3) {
    const hours = Number(parts[0])
    const minutes = Number(parts[1])
    const seconds = Number(parts[2])
    if (
      Number.isFinite(hours) &&
      Number.isFinite(minutes) &&
      Number.isFinite(seconds)
    ) {
      return hours * 3600 + minutes * 60 + seconds
    }
  }

  return Number.NaN
}

export function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => {
    const map: Record<string, string> = {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#039;',
    }
    return map[char] ?? char
  })
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
