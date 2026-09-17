import {
  statusEvent,
  TERMINAL,
  asRecord,
  asText,
  type ChatMessage,
} from './studioChatMessages'

/** 已被后续事件了结的 cancel_requested 状态消息 id（#675 codex P2）：
 * 「等待 agent 收尾」只在收尾尚未发生时是当前态——其后一旦出现 turn 终止
 * 事件（TERMINAL：turn_end/error/session_closed/session_resumed，turn_end
 * 本身被 StatusLine 隐藏）或新一轮用户消息（后端重启丢失 turn_end 时的
 * 兜底，与 MessageItem 的空闲兜底同源），该行必须降级为历史记录，否则
 * 会与 RunBar 的「已取消」永久冲突。收尾窗口内的 tool_call/thought/
 * agent text 不了结——那正是等待期本身。 */
export function supersededCancelRequestIds(
  messages: ChatMessage[]
): Set<string> {
  const superseded = new Set<string>()
  const pending: string[] = []
  const settle = () => {
    for (const id of pending) superseded.add(id)
    pending.length = 0
  }
  for (const message of messages) {
    if (message.kind === 'status') {
      const event = statusEvent(message).event
      if (event === 'cancel_requested') pending.push(message.id)
      else if (TERMINAL.has(event)) settle()
      continue
    }
    if (message.kind === 'text' && message.role === 'user') settle()
  }
  return superseded
}

/** turn_end 状态行的停止原因（ACP stopReason，后端 events.on_turn_end 透传）。 */
export function stopReason(message: ChatMessage): string {
  return asText(asRecord(message.content)?.stop_reason)
}

/** 最近一次已收尾的运行是否以「取消」结束：从尾部扫描，先撞到的终止状态
 * （turn_end/error/session_closed/session_resumed）定夺——stop_reason 为
 * cancelled 的 turn_end 即取消；后续新一轮的用户消息不再改变上一轮的结论
 * （新一轮 busy 期间本视图不被 RunBar 消费）。 */
export function lastRunCancelled(messages: ChatMessage[]): boolean {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i]
    if (m.kind !== 'status' || !TERMINAL.has(statusEvent(m).event)) continue
    return statusEvent(m).event === 'turn_end' && stopReason(m) === 'cancelled'
  }
  return false
}
