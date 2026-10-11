import { useEffect, useRef } from 'react'
import type { ChatMessage } from './studioChatMessages'

/** 消息列表自动滚动：仅在用户本已贴底时跟随滚动到底——向上翻历史时不被
 * 流式分片拽回底部（jsdom 没有 scrollIntoView，测试中恒不滚动）。
 * 滚动到顶（<80px）时回调 onNearTop(list) 加载更早一页（#1120 PR-4）；
 * prepend 的视口锚定补偿在 useChatPrependAnchor。 */
export function useChatAutoScroll(
  messages: ChatMessage[],
  onNearTop?: (list: HTMLElement) => void
) {
  const bottomRef = useRef<HTMLDivElement | null>(null)
  const listRef = useRef<HTMLDivElement | null>(null)
  const pinnedToBottomRef = useRef(true)

  useEffect(() => {
    if (pinnedToBottomRef.current)
      bottomRef.current?.scrollIntoView?.({ block: 'end' })
  }, [messages])

  function handleScroll() {
    const el = listRef.current
    if (!el) return
    pinnedToBottomRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < 80
    if (el.scrollTop < 80) onNearTop?.(el)
  }

  return { bottomRef, listRef, handleScroll, pinnedToBottomRef }
}
