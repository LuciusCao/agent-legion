import { statusEvent, type ChatMessage } from './studioChatMessages'
import styles from './StudioChatPanel.module.css'
import { StatusWarning } from './StudioChatStatusWarning'

type Props = { message: ChatMessage; cancelSuperseded?: boolean }

export function StatusLine({ message, cancelSuperseded = false }: Props) {
  const { event, detail } = statusEvent(message)
  if (event === 'turn_end') return null
  if (
    event === 'turn_timeout' ||
    event === 'empty_turn' ||
    event === 'run_token_invalidated' ||
    event === 'error'
  ) {
    return <StatusWarning message={message} />
  }
  if (event === 'mcp_unverified') {
    // 文案以后端 detail 为唯一来源（mcp_hint.MCP_UNVERIFIED_HINT）。
    return (
      <div className={styles.statusLine}>
        ℹ {detail || '本会话尚未观察到 agent-legion 平台工具调用'}
      </div>
    )
  }
  // #675 codex P2：cancel_requested 是时间线里的持久记录，「等待收尾」只是
  // 当时的当前态；终止事件/新一轮到达后（cancelSuperseded）降级为历史措辞，
  // 不再与 RunBar 的「已取消」冲突。
  const text =
    event === 'cancel_requested'
      ? cancelSuperseded
        ? '已请求取消'
        : '已请求取消当前运行，等待 agent 收尾'
      : event === 'session_closed'
        ? '会话已关闭'
        : event === 'session_resumed'
          ? '会话已恢复，可继续对话'
          : detail || event
  return <div className={styles.statusLine}>{text}</div>
}
