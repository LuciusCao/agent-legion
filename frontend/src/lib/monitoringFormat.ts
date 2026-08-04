import type { OpsGranularity } from '../api/metrics'

/** 千分位整数；无数据显示占位符。 */
export function fmt(value: number | null | undefined) {
  return typeof value === 'number' ? value.toLocaleString('zh-CN') : '-'
}

/** 耗时展示：不足 1 分钟按秒，否则「Xm YYs」；无数据（窗口内无完成 run）显示占位符。 */
export function fmtDuration(seconds: number | null | undefined) {
  if (typeof seconds !== 'number') return '-'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return `${minutes}m ${pad(rest)}s`
}

export function pad(n: number) {
  return String(n).padStart(2, '0')
}

export function makeTimeFormatter(granularity: OpsGranularity) {
  return (iso: string) => {
    const d = new Date(iso)
    // 30d 的 4 小时桶需要「日期 + 小时」才能区分同一天内的多个桶
    if (granularity === '30d')
      return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:00`
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`
  }
}
