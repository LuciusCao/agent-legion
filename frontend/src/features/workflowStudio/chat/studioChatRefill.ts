import { upsertMessage, type ChatMessage } from './studioChatMessages'
import type { StudioChatMessageRecord } from './studioChatApi'

/** 增量/全量补齐的合入：逐条 upsert，未知 id 残片留待后续事件再触发补齐。 */
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
