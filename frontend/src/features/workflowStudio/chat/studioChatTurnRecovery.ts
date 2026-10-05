import { useMemo } from 'react'
import {
  asRecord,
  asText,
  statusEvent,
  TERMINAL,
  type ChatMessage,
} from './studioChatMessages'

/** #882：空闲态「继续对话」的可达判定——最近一轮被后端宽限复核确认为零内容
 * （empty_turn 带 message_id），且之后没有任何新内容/新一轮/重投记录。从尾部
 * 扫描：先撞到 empty_turn 即可重投；先撞到任何非状态消息（用户或 agent 内容）、
 * 终止事件（新一轮的 turn_end 等）或重投 / 排队投递记录则不可重投。后端以
 * 内存槽位做唯一裁决（双击、多标签页只投递一次），这里只决定按钮是否出现。 */
export function emptyTurnRetryPending(messages: ChatMessage[]): boolean {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i]
    if (message.kind !== 'status') return false
    const { event } = statusEvent(message)
    if (event === 'empty_turn')
      return asText(asRecord(message.content)?.message_id) !== ''
    if (
      TERMINAL.has(event) ||
      event === 'empty_turn_retry' ||
      event === 'queued_delivered'
    )
      return false
  }
  return false
}

export type QueuedMessageState = 'pending' | 'dropped'

/** #882：后台唤醒轮占用会话期间发出的消息由后端入站排队（content.queued），
 * 轮到它时以 queued_delivered / queued_dropped 状态行了结。返回仍需标注的
 * 用户消息：pending =「已排队」；dropped =「未送达」（后端放弃投递，或会话
 * 关闭 / 恢复重建 / 当前不再存活，排队随旧 runtime 一并失效）。 */
export function queuedMessageStates(
  messages: ChatMessage[],
  live: boolean
): Map<string, QueuedMessageState> {
  const states = new Map<string, QueuedMessageState>()
  for (const message of messages) {
    const content = asRecord(message.content)
    if (message.kind === 'text' && message.role === 'user') {
      if (content?.queued === true) states.set(message.id, 'pending')
      continue
    }
    if (message.kind !== 'status') continue
    const { event } = statusEvent(message)
    const id = asText(content?.message_id)
    if (event === 'queued_delivered') states.delete(id)
    else if (event === 'queued_dropped' && states.has(id))
      states.set(id, 'dropped')
    else if (event === 'session_closed' || event === 'session_resumed')
      for (const key of states.keys()) states.set(key, 'dropped')
  }
  if (!live) for (const key of states.keys()) states.set(key, 'dropped')
  return states
}

/** 消息列表用的 memo 包装（closed = 会话已关闭/出错，排队随之失效）。 */
export function useQueuedMessageStates(
  messages: ChatMessage[],
  closed: boolean
): Map<string, QueuedMessageState> {
  return useMemo(
    () => queuedMessageStates(messages, !closed),
    [messages, closed]
  )
}
