import type { StudioChat } from './useStudioChat'
import type { useStudioChatQueue } from './useStudioChatQueue'
import styles from './AgentChatStatusStrip.module.css'

type Props = {
  chat: StudioChat
  queue: ReturnType<typeof useStudioChatQueue>
}

function formatDuration(ms: number): string {
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m${seconds % 60}s`
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function formatTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

/** #695 R3：ContextMeter / RunBar / ResumeBar 收敛成的单行 status strip——
 * 左侧运行状态（运行中+取消 / 已完成·用时 / 已超时终止 / 恢复入口）与排队
 * 摘要，右侧上下文用量（压缩提示并入）；各槽位无内容不占位，全空则整行
 * 不渲染。队列摘要只放「排队中 N」：排队文本与逐条移除保留在下方独立的
 * StudioChatQueueBar 行（仅队列非空时出现）——排队消息是用户待发内容，
 * 收进 popover 要多一次点击才能查看/移除，取舍为可见性优先；常态 idle 无
 * 队列时该行不出现，信息密度目标不受影响。 */
export function AgentChatStatusStrip({ chat, queue }: Props) {
  const status = chat.session?.status ?? null
  const compacting = chat.session?.compacting ?? false
  const queued = queue.queuedMessages.length

  const used = asNumber(chat.session?.usage?.used)
  const size = asNumber(chat.session?.usage?.size)
  const hasUsage = used !== null && size !== null && size > 0
  const percent = hasUsage ? Math.min(100, Math.round((used / size) * 100)) : 0

  const resumable = chat.closed && chat.session !== null
  let runSlot = null
  if (resumable) {
    const interrupted = chat.session?.status === 'error'
    runSlot = (
      <>
        <span className={`${styles.dot} ${styles.dotError}`} />
        <span className={styles.label}>
          {interrupted ? '会话已中断' : '会话已关闭'}，历史记录已保留
        </span>
        <button
          type="button"
          className={styles.button}
          disabled={chat.resuming}
          onClick={() => void chat.resume()}
        >
          {chat.resuming ? '正在恢复…' : '继续对话'}
        </button>
      </>
    )
  } else if (chat.busy && status) {
    const label =
      status === 'awaiting_permission'
        ? '等待权限确认'
        : status === 'starting'
          ? '正在启动 agent'
          : '运行中'
    runSlot = (
      <>
        <span className={`${styles.dot} ${styles.dotBusy}`} />
        <span className={styles.label}>{label}</span>
        <button
          type="button"
          className={`${styles.button} ${styles.cancelButton}`}
          onClick={() => void chat.cancel()}
        >
          取消
        </button>
      </>
    )
  } else if (chat.lastRunMs !== null) {
    // #693：被平台超时终止的轮次不能显示「已完成」。
    const timedOut = chat.lastTerminalEvent === 'turn_timeout'
    runSlot = (
      <>
        <span
          className={`${styles.dot} ${timedOut ? styles.dotError : styles.dotDone}`}
        />
        <span className={styles.label}>
          {timedOut ? '已超时终止' : '已完成'} · 用时{' '}
          {formatDuration(chat.lastRunMs)}
        </span>
      </>
    )
  }

  if (!runSlot && queued === 0 && !hasUsage && !compacting) return null
  return (
    <div className={styles.strip} aria-label="会话状态条">
      {runSlot && (
        <span className={styles.runState} aria-label="运行状态">
          {runSlot}
        </span>
      )}
      {queued > 0 && <span className={styles.queue}>排队中 {queued}</span>}
      {(hasUsage || compacting) && (
        <span className={styles.meter} aria-label="上下文用量">
          {hasUsage && (
            <span>
              上下文 {formatTokens(used)} / {formatTokens(size)} tokens（
              {percent}%）
            </span>
          )}
          {compacting && <span>正在压缩上下文…</span>}
        </span>
      )}
    </div>
  )
}
