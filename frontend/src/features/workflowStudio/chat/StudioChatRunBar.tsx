import styles from './StudioChatPanel.module.css'

type Props = {
  status: string | null
  busy: boolean
  lastRunMs: number | null
  lastTerminalEvent: string | null
  lastRunCancelled: boolean
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
        <span>会话出错，可点「继续对话」恢复</span>
      </div>
    )
  }
  // #675：取消（stopReason=cancelled）不是失败也不是「已完成」——
  // 中断时被派发的子代理在 CLI 内部继续跑完是常见实证，工具卡片里的
  // 末次状态才是真实收尾；取消轮之后的下一条消息可让它继续收尾汇报。
  if (props.lastRunCancelled) {
    return (
      <div className={styles.runBar} aria-label="运行状态">
        <span className={`${styles.runDot} ${styles.runDotDone}`} />
        <span>
          已取消
          {props.lastRunMs !== null
            ? ` · 已运行 ${formatDuration(props.lastRunMs)}`
            : ''}
          ，agent 未收尾的工作可继续追问结果
        </span>
      </div>
    )
  }
  if (props.lastRunMs !== null) {
    // #693：被平台超时终止的轮次不能显示「已完成」。
    if (props.lastTerminalEvent === 'turn_timeout') {
      return (
        <div className={styles.runBar} aria-label="运行状态">
          <span className={`${styles.runDot} ${styles.runDotError}`} />
          <span>已超时终止 · 用时 {formatDuration(props.lastRunMs)}</span>
        </div>
      )
    }
    return (
      <div className={styles.runBar} aria-label="运行状态">
        <span className={`${styles.runDot} ${styles.runDotDone}`} />
        <span>已完成 · 用时 {formatDuration(props.lastRunMs)}</span>
      </div>
    )
  }
  return null
}
