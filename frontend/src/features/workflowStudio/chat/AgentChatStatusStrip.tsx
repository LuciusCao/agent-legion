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

/** #695 R3：RunBar / ResumeBar 收敛成的状态行——左侧运行状态（运行中+取消 /
 * 已完成·用时 / 已超时终止 / 已取消 #675 / 恢复入口），右侧排队摘要；各槽位
 * 无内容不占位，全空则整行不渲染。上下文用量在 composer 工具行的圆环
 * （StudioChatContextRing，含压缩提示）。本行渲染在输入卡片外、composer 上方
 * （#787：取消是破坏性动作，不收进输入卡片）。队列摘要只放「排队中 N」：排队
 * 文本与逐条移除保留在卡外独立的 StudioChatQueueBar 行（仅队列非空时出现）
 * ——排队消息是用户待发内容，收进 popover 要多一次点击才能查看/移除，取舍为
 * 可见性优先；常态 idle 无队列时该行不出现，信息密度目标不受影响。 */
export function AgentChatStatusStrip({ chat, queue }: Props) {
  const status = chat.session?.status ?? null
  const queued = queue.queuedMessages.length

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
  } else if (chat.lastRunCancelled) {
    // #675：取消（stopReason=cancelled）不是失败也不是「已完成」——中断时
    // 被派发的子代理在 CLI 内部继续跑完是常见实证，工具卡片里的末次状态
    // 才是真实收尾；取消轮之后的下一条消息可让它继续收尾汇报。
    runSlot = (
      <>
        <span className={`${styles.dot} ${styles.dotDone}`} />
        <span className={styles.label}>
          已取消
          {chat.lastRunMs !== null
            ? ` · 已运行 ${formatDuration(chat.lastRunMs)}`
            : ''}
          ，agent 未收尾的工作可继续追问结果
        </span>
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

  if (!runSlot && queued === 0) return null
  return (
    <div className={styles.strip} aria-label="会话状态条">
      {runSlot && (
        <span className={styles.runState} aria-label="运行状态">
          {runSlot}
        </span>
      )}
      {queued > 0 && <span className={styles.queue}>排队中 {queued}</span>}
    </div>
  )
}
