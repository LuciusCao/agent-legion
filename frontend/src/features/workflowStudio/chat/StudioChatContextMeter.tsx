import styles from './StudioChatPanel.module.css'
import type { StudioChatSessionRecord } from './studioChatApi'

type Props = {
  usage: StudioChatSessionRecord['usage']
  compacting: boolean
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function formatTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

/** #694：上下文容量条——ACP usage_update 的镜像（used = 当前上下文内
 * token 数，size = 上下文窗口）；压缩窗口内追加提示，与输入框禁用同源。 */
export function StudioChatContextMeter(props: Props) {
  const used = asNumber(props.usage?.used)
  const size = asNumber(props.usage?.size)
  const hasUsage = used !== null && size !== null && size > 0
  if (!hasUsage && !props.compacting) return null
  const percent = hasUsage ? Math.min(100, Math.round((used / size) * 100)) : 0
  return (
    <div className={styles.runBar} aria-label="上下文用量">
      {hasUsage && (
        <span>
          上下文 {formatTokens(used)} / {formatTokens(size)} tokens（{percent}
          %）
        </span>
      )}
      {props.compacting && <span>正在压缩上下文…</span>}
    </div>
  )
}
