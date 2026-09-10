import {
  statusEvent,
  upsertMessage,
  type ChatMessage,
} from './studioChatMessages'
import type { StudioChatMessageRecord } from './studioChatApi'

/** 增量/全量补齐的合入：逐条 upsert，未知 id 残片留待后续事件再触发补齐。
 * hasTerminal = 补齐携带 terminal 状态行（turn_end/error/session_*）：REST
 * 静默合入的 turn_end 必须与 SSE 实时到达的同等触发全量回取，否则截断的
 * 流式 text 副本永久定格（原地更新 seq 不变，after_seq 取不回，#563）。 */
export function mergeMessages(
  current: ChatMessage[],
  fetched: StudioChatMessageRecord[]
): { messages: ChatMessage[]; hasTerminal: boolean } {
  let next = current
  let hasTerminal = false
  for (const message of fetched) {
    next = upsertMessage(next, message) ?? next
    if (isTurnEnd(message)) hasTerminal = true
  }
  return { messages: next, hasTerminal }
}

/** SSE/REST 双路径的 turn_end 检测（与 mergeMessages 的 terminal 判定同源）。 */
export function isTurnEnd(message: ChatMessage): boolean {
  return message.kind === 'status' && statusEvent(message).event === 'turn_end'
}
