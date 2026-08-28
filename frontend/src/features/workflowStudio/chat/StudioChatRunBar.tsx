import styles from './StudioChatPanel.module.css'

type Props = {
  status: string | null
  busy: boolean
  lastRunMs: number | null
  onCancel: () => void
}

function formatDuration(ms: number): string {
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m${seconds % 60}s`
}

export function StudioChatRunBar(props: Props) {
  if (!props.status) return null
  if (props.busy) {
    const label =
      props.status === 'awaiting_permission'
        ? '等待权限确认'
        : props.status === 'starting'
          ? '正在启动 agent'
          : '运行中'
    return (
      <div className={styles.runBar} aria-label="运行状态">
        <span className={`${styles.runDot} ${styles.runDotBusy}`} />
        <span>{label}</span>
        <button
          type="button"
          className={styles.cancelButton}
          onClick={props.onCancel}
        >
          取消
        </button>
      </div>
    )
  }
  if (props.status === 'error') {
    return (
      <div className={styles.runBar} aria-label="运行状态">
        <span className={`${styles.runDot} ${styles.runDotError}`} />
        <span>会话出错，请新建对话</span>
      </div>
    )
  }
  if (props.lastRunMs !== null) {
    return (
      <div className={styles.runBar} aria-label="运行状态">
        <span className={`${styles.runDot} ${styles.runDotDone}`} />
        <span>已完成 · 用时 {formatDuration(props.lastRunMs)}</span>
      </div>
    )
  }
  return null
}
