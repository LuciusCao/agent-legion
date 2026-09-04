import { statusEvent, type ChatMessage } from './studioChatMessages'
import styles from './StudioChatPanel.module.css'

export function StatusLine({ message }: { message: ChatMessage }) {
  const { event, detail } = statusEvent(message)
  if (event === 'turn_end') return null
  if (event === 'mcp_unverified') {
    // 文案以后端 detail 为唯一来源（mcp_hint.MCP_UNVERIFIED_HINT）。
    return (
      <div className={styles.statusLine}>
        ℹ {detail || '本会话尚未观察到 agent-legion 平台工具调用'}
      </div>
    )
  }
  if (event === 'run_token_invalidated') {
    // run token 过期/吊销：工具通道死亡但聊天主链路仍活着（#411），必须
    // 显式警示而不是静默失败；文案以后端 detail 为唯一来源（「继续对话」
    // 按钮仅在会话关闭后出现，故恢复指引包含先关闭）。
    return (
      <div className={styles.statusWarning} role="alert">
        ⚠ {detail || '工具通道已失效，关闭会话后点「继续对话」恢复'}
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
        : event === 'session_resumed'
          ? '会话已恢复，可继续对话'
          : detail || event
  return <div className={styles.statusLine}>{text}</div>
}
