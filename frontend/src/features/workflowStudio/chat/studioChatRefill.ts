import { statusEvent, upsertMessage, type ChatMessage } from './studioChatMessages'
import type { StudioChatMessageRecord } from './studioChatApi'

// 与 studioChatMessages 的 TERMINAL 集合同义：任一到达即该 turn 已终结，
// 之前仍在流式聚合的 agent text 行已是最终文本，值得一次全量回取校准。
const TERMINAL_EVENTS = new Set([
  'turn_end',
  'error',
  'session_closed',
  'session_resumed',
])

/** 增量/全量补齐的合入：逐条 upsert，任一未知 id 的残片保持现状由后续
 * 事件再触发补齐。返回合入后是否包含 terminal 状态行——REST 补齐静默
 * 携带的 turn_end（断连空窗盖过 turn 结尾时唯一到达路径）必须与 SSE 实时
 * 到达的 turn_end 同等触发全量回取，否则流式 text 的截断副本永久定格
 * （原地更新 seq 不变，after_seq 增量补齐取不回该行，#563）。 */
export function mergeMessages(
  current: ChatMessage[],
  fetched: StudioChatMessageRecord[]
): { messages: ChatMessage[]; hasTerminal: boolean } {
  let next = current
  let hasTerminal = false
  for (const message of fetched) {
    next = upsertMessage(next, message) ?? next
    if (
      message.kind === 'status' &&
      TERMINAL_EVENTS.has(statusEvent(message).event)
    ) {
      hasTerminal = true
    }
  }
  return { messages: next, hasTerminal }
}
