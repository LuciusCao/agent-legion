import { useCallback, useEffect, useLayoutEffect, useRef } from 'react'
import type { ChatMessage } from './studioChatMessages'

type RowAnchor = { element: Element; top: number } | null

/** 列表内第一行可见消息元素 + 其相对容器顶部的偏移。虚拟态的行在 spacer
 * （直接子行带 data-index 的容器）里，非虚拟态直接是列表子元素（末尾的
 * bottom 占位 div 高度为 0，贴顶触发时永远不会被选中）。 */
function rowElements(list: HTMLElement): Element[] {
  const rows: Element[] = []
  for (const child of list.children) {
    const virtualRows = child.querySelectorAll(':scope > [data-index]')
    if (virtualRows.length > 0) rows.push(...virtualRows)
    else rows.push(child)
  }
  return rows
}

function firstVisibleRow(list: HTMLElement): RowAnchor {
  const top = list.getBoundingClientRect().top
  for (const row of rowElements(list)) {
    const rect = row.getBoundingClientRect()
    if (rect.bottom > top) return { element: row, top: rect.top - top }
  }
  return null
}

/** prepend 滚动锚定（#1120 PR-4）：滚动到顶触发分页时捕获首行可见元素的
 * 位置快照；prepend commit 后按同一元素的实测位移补偿 scrollTop——触发到
 * 响应之间的内容高度变化（虚拟器测量修正、卡片展开）不混入补偿量
 * （#1237 codex P2-1：高度差分基线在两次快照之间会被这类修正污染）。
 * 锚定行在 prepend 后被虚拟器裁出挂载窗口（isConnected=false）时回退
 * scrollHeight 差分，其基线除触发时刷新外由 ResizeObserver 持续同步
 * （prepend 的 commit 先走 layout effect 补偿、RO 回调后 fire，顺序安全）。
 * 与 useChatAutoScroll 组合使用：返回的回调作为其 onNearTop。 */
export function useChatPrependAnchor(
  messages: ChatMessage[],
  onLoadOlder: () => void
): (list: HTMLElement) => void {
  const anchorRef = useRef<{
    firstId: string | null
    list: HTMLElement | null
    row: RowAnchor
    scrollHeight: number
  }>({ firstId: null, list: null, row: null, scrollHeight: 0 })
  const observerRef = useRef<ResizeObserver | null>(null)

  useEffect(() => () => observerRef.current?.disconnect(), [])

  useLayoutEffect(() => {
    const anchor = anchorRef.current
    const firstId = messages[0]?.id ?? null
    const el = anchor.list
    // prepend 检测：首条消息 id 变了且旧首条仍在列表中（追加/整体替换不补偿）。
    if (
      el &&
      anchor.firstId !== null &&
      firstId !== anchor.firstId &&
      messages.some((message) => message.id === anchor.firstId)
    ) {
      const row = anchor.row
      if (row && row.element.isConnected) {
        // 按锚定行实测位移复原视口。
        el.scrollTop +=
          row.element.getBoundingClientRect().top -
          el.getBoundingClientRect().top -
          row.top
      } else {
        // 锚定行已被虚拟器裁出挂载窗口：回退高度差分。
        el.scrollTop += el.scrollHeight - anchor.scrollHeight
      }
    }
    anchor.firstId = firstId
  }, [messages])

  return useCallback(
    (list: HTMLElement) => {
      const anchor = anchorRef.current
      anchor.list = list
      anchor.scrollHeight = list.scrollHeight
      anchor.row = firstVisibleRow(list)
      // 差分基线的持续同步（回退路径用）：观察内容行/spacer 的高度变化。
      if (!observerRef.current && typeof ResizeObserver !== 'undefined') {
        const observer = new ResizeObserver(() => {
          anchorRef.current.scrollHeight = list.scrollHeight
        })
        for (const child of list.children) observer.observe(child)
        observerRef.current = observer
      }
      onLoadOlder()
    },
    [onLoadOlder]
  )
}
