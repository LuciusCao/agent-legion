import { useEffect, useLayoutEffect, useRef } from 'react'
import type { ChatMessage } from './studioChatMessages'

/** 消息列表自动滚动：仅在用户本已贴底时跟随滚动到底——向上翻历史时不被
 * 流式分片拽回底部（jsdom 没有 scrollIntoView，测试中恒不滚动）。
 * 滚动到顶（<80px）时回调 onNearTop 加载更早一页（#1120 PR-4）。
 * 锚定补偿：检测到 prepend（首条消息 id 变了且旧首条仍在列表中）时，按
 * scrollHeight 增量补偿 scrollTop——基线在每次渲染的 layout effect 与
 * 每次到顶触发时快照（#1237 codex P2-2：虚拟器的测量修正不改 messages，
 * 只在消息变化时快照会把累计修正混进补偿增量）；追加/切换会话不补偿。 */
export function useChatAutoScroll(
  messages: ChatMessage[],
  onNearTop?: () => void
) {
  const bottomRef = useRef<HTMLDivElement | null>(null)
  const listRef = useRef<HTMLDivElement | null>(null)
  const pinnedToBottomRef = useRef(true)
  const anchorRef = useRef({ firstId: null as string | null, scrollHeight: 0 })

  useEffect(() => {
    if (pinnedToBottomRef.current)
      bottomRef.current?.scrollIntoView?.({ block: 'end' })
  }, [messages])

  useLayoutEffect(() => {
    const el = listRef.current
    const firstId = messages[0]?.id ?? null
    if (el) {
      const anchor = anchorRef.current
      if (
        anchor.firstId !== null &&
        firstId !== anchor.firstId &&
        messages.some((message) => message.id === anchor.firstId)
      ) {
        // prepend：新行把旧内容往下推了 scrollHeight 增量，等值补偿 scrollTop。
        el.scrollTop += el.scrollHeight - anchor.scrollHeight
      }
      anchor.scrollHeight = el.scrollHeight
    }
    anchorRef.current.firstId = firstId
  }, [messages])

  function handleScroll() {
    const el = listRef.current
    if (!el) return
    pinnedToBottomRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < 80
    if (el.scrollTop < 80) {
      // 到顶触发分页：同时把补偿基线刷新为当前高度（含虚拟器测量修正）。
      anchorRef.current.scrollHeight = el.scrollHeight
      onNearTop?.()
    }
  }

  return { bottomRef, listRef, handleScroll, pinnedToBottomRef }
}
