import { useEffect, useLayoutEffect, useRef } from 'react'
import type { ChatMessage } from './studioChatMessages'

/** 消息列表自动滚动：仅在用户本已贴底时跟随滚动到底——向上翻历史时不被
 * 流式分片拽回底部（jsdom 没有 scrollIntoView，测试中恒不滚动）。
 * 滚动到顶（<80px）时回调 onNearTop 加载更早一页（#1120 PR-4）。
 * 锚定补偿：检测到 prepend（首条消息 id 变了且旧首条仍在列表中）时，按
 * scrollHeight 增量补偿 scrollTop——prepend 前记录的高度在每次渲染的
 * layout effect 里快照，用户视口无跳动感知；追加/切换会话/全量替换不补偿。 */
export function useChatAutoScroll(
  messages: ChatMessage[],
  onNearTop?: () => void
) {
  const bottomRef = useRef<HTMLDivElement | null>(null)
  const listRef = useRef<HTMLDivElement | null>(null)
  const pinnedToBottomRef = useRef(true)
  const onNearTopRef = useRef(onNearTop)
  const firstIdRef = useRef<string | null>(null)
  const scrollHeightRef = useRef(0)

  useEffect(() => {
    if (pinnedToBottomRef.current)
      bottomRef.current?.scrollIntoView?.({ block: 'end' })
  }, [messages])

  useLayoutEffect(() => {
    onNearTopRef.current = onNearTop
    const el = listRef.current
    const firstId = messages[0]?.id ?? null
    if (el) {
      const previousFirstId = firstIdRef.current
      if (
        previousFirstId !== null &&
        firstId !== previousFirstId &&
        messages.some((message) => message.id === previousFirstId)
      ) {
        // prepend：新行把旧内容往下推了 scrollHeight 增量，等值补偿 scrollTop。
        el.scrollTop += el.scrollHeight - scrollHeightRef.current
      }
      scrollHeightRef.current = el.scrollHeight
    }
    firstIdRef.current = firstId
  }, [messages, onNearTop])

  function handleScroll() {
    const el = listRef.current
    if (!el) return
    pinnedToBottomRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < 80
    if (el.scrollTop < 80) onNearTopRef.current?.()
  }

  return { bottomRef, listRef, handleScroll, pinnedToBottomRef }
}
