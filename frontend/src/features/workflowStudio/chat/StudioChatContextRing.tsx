import { Tooltip } from '@mui/material'
import styles from './StudioChatContextRing.module.css'

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

function asTokenCount(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** 从会话记录的松散 usage 字典（generated 类型为 Record<string, unknown>）
 * 收窄出圆环所需的两个数；字段缺失/非数值时对应位为 null（圆环不渲染弧）。 */
export function contextUsageFromSession(
  session: { usage?: { [key: string]: unknown } | null } | null | undefined
): { used: number | null; size: number | null } | null {
  const usage = session?.usage
  if (!usage) return null
  return { used: asTokenCount(usage.used), size: asTokenCount(usage.size) }
}

const RADIUS = 7.5
const CIRCUMFERENCE = 2 * Math.PI * RADIUS

/** 上下文用量圆环（composer 工具行内嵌）：弧长表达占上下文窗口的百分比，
 * 环旁不常驻数字；hover 经 Tooltip 展示精确 token 数与百分比（≥1M 用 M
 * 单位）。占比分档配色（<70% 蓝 / 70–90% 琥珀 / >90% 红），压缩窗口内整环
 * 脉冲并提示。无用量数据且非压缩中时不渲染。 */
export function StudioChatContextRing(props: {
  used: number | null
  size: number | null
  compacting: boolean
}) {
  const hasUsage = props.used !== null && props.size !== null && props.size > 0
  if (!hasUsage && !props.compacting) return null
  const percent = hasUsage
    ? Math.min(100, Math.round((props.used! / props.size!) * 100))
    : 0
  const level = !hasUsage
    ? 'idle'
    : percent >= 90
      ? 'high'
      : percent >= 70
        ? 'mid'
        : 'low'
  const detail = hasUsage
    ? `上下文 ${formatTokens(props.used!)} / ${formatTokens(props.size!)} tokens（${percent}%）`
    : ''
  const tip = props.compacting
    ? `${detail}${detail ? ' · ' : ''}正在压缩上下文…`
    : detail
  return (
    <Tooltip title={tip} arrow placement="top">
      <span
        className={`${styles.ring} ${styles[level]}${props.compacting ? ` ${styles.compacting}` : ''}`}
        aria-label="上下文用量"
      >
        <svg viewBox="0 0 18 18" width="18" height="18" aria-hidden="true">
          <circle className={styles.track} cx="9" cy="9" r={RADIUS} />
          {hasUsage && (
            <circle
              className={styles.arc}
              cx="9"
              cy="9"
              r={RADIUS}
              strokeDasharray={`${(percent / 100) * CIRCUMFERENCE} ${CIRCUMFERENCE}`}
              transform="rotate(-90 9 9)"
            />
          )}
        </svg>
      </span>
    </Tooltip>
  )
}
