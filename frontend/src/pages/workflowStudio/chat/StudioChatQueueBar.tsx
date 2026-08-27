import type { useStudioChatQueue } from './useStudioChatQueue'
import styles from './StudioChatQueueBar.module.css'

type Props = {
  queue: ReturnType<typeof useStudioChatQueue>
}

/** 输入框上方的发送队列条：busy 时入队的消息在运行结束后按 FIFO 自动发出，
 * 也可逐条手动移除。 */
export function StudioChatQueueBar(props: Props) {
  if (props.queue.queuedMessages.length === 0) return null
  return (
    <div className={styles.queueBar} aria-label="发送队列">
      {props.queue.queuedMessages.map((item, index) => (
        <div key={item.id} className={styles.queueItem}>
          <span className={styles.queueBadge}>排队中 {index + 1}</span>
          <span className={styles.queueText}>{item.text}</span>
          <button
            type="button"
            className={styles.queueRemove}
            onClick={() => props.queue.remove(item.id)}
          >
            移除
          </button>
        </div>
      ))}
    </div>
  )
}
