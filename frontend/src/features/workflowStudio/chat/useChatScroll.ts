import { useChatAutoScroll } from './useChatAutoScroll'
import { useChatPrependAnchor } from './useChatPrependAnchor'
import type { ChatMessage } from './studioChatMessages'

/** 消息列表滚动行为组合（#1120 PR-4）：自动跟底 + 到顶/未溢出加载更早 +
 * prepend 锚定补偿。拆自 StudioChatMessageList（文件预算）。 */
export function useChatScroll(
  messages: ChatMessage[],
  onLoadOlder: () => void
) {
  return useChatAutoScroll(
    messages,
    useChatPrependAnchor(messages, onLoadOlder)
  )
}
