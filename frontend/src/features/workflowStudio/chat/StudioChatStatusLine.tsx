import { statusEvent, type ChatMessage } from './studioChatMessages'
import styles from './StudioChatPanel.module.css'
// #695：错误/告警条样式归 AgentChatPanel 骨架（红=statusError，原
// StudioChatPanel.module.css 的 statusWarning 已随语义拆分退役）。
import shellStyles from './AgentChatPanel.module.css'

export function StatusLine({ message }: { message: ChatMessage }) {
  const { event, detail } = statusEvent(message)
  if (event === 'turn_end') return null
  if (event === 'turn_timeout') {
    // #693：运行超过平台时限被终止——文案以后端 detail 为唯一来源。
    return (
      <div className={shellStyles.statusError} role="alert">
        ⚠ {detail || '运行超过 1 小时已被终止'}
      </div>
    )
  }
  if (event === 'empty_turn') {
    // #694：瞬时零内容的假 end_turn（静默排队签名）——提示重发/继续对话。
    return (
      <div className={shellStyles.statusError} role="alert">
        ⚠ {detail || 'agent 未实际处理这条消息，请稍后重发'}
      </div>
    )
  }
  if (event === 'mcp_unverified') {
    // 文案以后端 detail 为唯一来源（mcp_hint.MCP_UNVERIFIED_HINT）。
    return (
      <div className={styles.statusLine}>
        ℹ {detail || '本会话尚未观察到 agent-legion 平台工具调用'}
      </div>
    )
  }
  if (event === 'run_token_invalidated') {
    // run token 过期/吊销：工具通道死亡但聊天主链路仍活着（#411/#558——
    // 会话已被升级为 error，ResumeBar 的「继续对话」直接可达）。
    return (
      <div className={shellStyles.statusError} role="alert">
        ⚠ {detail || '工具通道已失效，点「继续对话」重建即可恢复'}
      </div>
    )
  }
  if (event === 'error') {
    return (
      <div className={shellStyles.statusError} role="alert">
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
