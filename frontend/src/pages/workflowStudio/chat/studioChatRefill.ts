import { upsertMessage, type ChatMessage } from './studioChatMessages'
import type { StudioChatMessageRecord } from './studioChatApi'

/** 增量/全量补齐的合入：逐条 upsert，任一未知 id 的残片保持现状由后续
 * 事件再触发补齐。 */
export function mergeMessages(
  current: ChatMessage[],
  fetched: StudioChatMessageRecord[]
): ChatMessage[] {
  let next = current
  for (const message of fetched) {
    next = upsertMessage(next, message) ?? next
  }
  return next
}
