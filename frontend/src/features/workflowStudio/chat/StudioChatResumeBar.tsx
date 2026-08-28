import type { StudioChat } from './useStudioChat'
import styles from './StudioChatResumeBar.module.css'

/** closed/error 会话的恢复条：历史消息已全量落库保留，点「继续对话」让
 * 后端重建 agent runtime（ACP session/load 或转录注入），恢复后可继续输入。 */
export function StudioChatResumeBar({ chat }: { chat: StudioChat }) {
  const interrupted = chat.session?.status === 'error'
  return (
    <div className={styles.resumeBar}>
      <span>
        {interrupted ? '会话已中断' : '会话已关闭'}
        ，历史记录已保留，可继续对话。
      </span>
      <button
        type="button"
        className={styles.resumeButton}
        disabled={chat.resuming}
        onClick={() => void chat.resume()}
      >
        {chat.resuming ? '正在恢复…' : '继续对话'}
      </button>
    </div>
  )
}
