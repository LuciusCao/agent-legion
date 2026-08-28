import { useEffect, useRef } from 'react'
import type { ChatMessage } from './studioChatMessages'

/** 消息列表自动滚动：仅在用户本已贴底时跟随滚动到底——向上翻历史时不被
 * 流式分片拽回底部（jsdom 没有 scrollIntoView，测试中恒不滚动）。 */
export function useChatAutoScroll(messages: ChatMessage[]) {
  const bottomRef = useRef<HTMLDivElement | null>(null)
  const listRef = useRef<HTMLDivElement | null>(null)
  const pinnedToBottomRef = useRef(true)

  useEffect(() => {
    if (pinnedToBottomRef.current)
      bottomRef.current?.scrollIntoView?.({ block: 'end' })
  }, [messages])

  function handleScroll() {
    const el = listRef.current
    if (el)
      pinnedToBottomRef.current =
        el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }

  return { bottomRef, listRef, handleScroll }
}
