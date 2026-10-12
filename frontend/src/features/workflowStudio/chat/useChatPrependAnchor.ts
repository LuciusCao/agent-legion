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

/** prepend 滚动锚定（#1120 PR-4）：滚动到顶触发分页时记录视口快照，
 * prepend commit 后补偿 scrollTop 使视口不跳。生产主路径是 scrollHeight
 * 差分（页大小远大于虚拟器挂载窗口，锚定行在 prepend 后几乎必被裁出），
 * 其基线除触发时刷新外由 ResizeObserver 持续同步——触发→响应窗口内的
 * 测量修正/卡片展开不混入补偿量（#1237 codex P2-1）。锚定行仍挂载时
 * （非虚拟态或短 prepend）按该元素的实测位移复原，比差分更精确。
 * （prepend 的 commit 先走 layout effect 补偿、RO 回调后 fire，顺序安全。）
 * 与 useChatAutoScroll 组合使用：返回的回调作为其 onNearTop。 */
export function useChatPrependAnchor(
  messages: ChatMessage[],
  onLoadOlder: (oldestSeq?: number) => void
): (list: HTMLElement) => void {
  const anchorRef = useRef<{
    firstId: string | null
    list: HTMLElement | null
    row: RowAnchor
    scrollHeight: number
    scrollTop: number
  }>({ firstId: null, list: null, row: null, scrollHeight: 0, scrollTop: 0 })
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
      messages.some((message) => message.id === anchor.firstId) &&
      // 加载在途期间用户已向下滚离锚点预期位：放弃恢复，不拽回旧锚点
      // （#1237 codex 七轮 P2）；贴顶区内微调与虚拟器修正漂移（≤80px）仍补偿。
      el.scrollTop - anchor.scrollTop <= 80
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
      anchor.scrollTop = list.scrollTop
      anchor.row = firstVisibleRow(list)
      // 差分基线的持续同步（回退路径是生产主路径）：观察当前内容行/spacer
      // 的高度变化。每次触发都按当前 children 重挂——非虚拟态长成/切入虚拟
      // 态时 spacer 是新 DOM 节点，只观察首次触发时的 children 会让基线
      // 同步静默失效（#1237 复验 P3-b）。
      if (typeof ResizeObserver !== 'undefined') {
        if (!observerRef.current) {
          observerRef.current = new ResizeObserver(() => {
            anchorRef.current.scrollHeight = list.scrollHeight
          })
        }
        const observer = observerRef.current
        observer.disconnect()
        for (const child of list.children) observer.observe(child)
      }
      onLoadOlder(messages[0]?.seq)
    },
    [onLoadOlder, messages]
  )
}
