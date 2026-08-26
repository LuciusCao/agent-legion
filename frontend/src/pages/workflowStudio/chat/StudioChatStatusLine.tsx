import { statusEvent, type ChatMessage } from './studioChatMessages'
import styles from './StudioChatPanel.module.css'

export function StatusLine({ message }: { message: ChatMessage }) {
  const { event, detail } = statusEvent(message)
  if (event === 'turn_end') return null
  if (event === 'mcp_unverified') {
    return (
      <div className={styles.statusWarning} role="alert">
        ⚠ 本轮没有调用任何 agent-legion 平台工具，agent 可能没有拿到 MCP
        工具，产出请人工核对。{detail}
      </div>
    )
  }
  if (event === 'error') {
    return (
      <div className={styles.statusWarning} role="alert">
        ⚠ {detail || 'agent 运行出错'}
      </div>
    )
  }
  const text =
    event === 'cancel_requested'
      ? '已请求取消当前运行'
      : event === 'session_closed'
        ? '会话已关闭'
        : detail || event
  return <div className={styles.statusLine}>{text}</div>
}
