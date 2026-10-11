import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from 'react'
import { fetchStudioChatHistoryPage } from './studioChatApi'
import { mergeMessages } from './studioChatRefill'
import type { ChatMessage } from './studioChatMessages'

/** 历史向上翻页（#1120 PR-4）：滚动到顶时以当前最老 seq 为游标（before_seq）
 * 拉取更早一页并 prepend——mergeMessages 按 id upsert + seq 排序，与
 * refillMessages(0) 全量回取重叠时幂等，中间的 seq gap 是设计接受态。
 * 状态机：loadingRef 防重入（加载中不重复拉）；hasMore=false 闩锁到顶终止；
 * 失败不推进游标也不闩锁 hasMore，下次滚到顶部自然重试。 */
export function useStudioChatHistory({
  workspaceId,
  activeSessionId,
  activeSessionIdRef,
  messagesRef,
  setMessages,
}: {
  workspaceId: string | undefined
  activeSessionId: string | null
  activeSessionIdRef: MutableRefObject<string | null>
  messagesRef: MutableRefObject<ChatMessage[]>
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>
}) {
  const [hasMoreHistory, setHasMoreHistory] = useState(true)
  const hasMoreRef = useRef(true)
  const loadingRef = useRef(false)

  // 切换会话/workspace：游标与到顶闩锁复位（消息列表由入口 effect 清空）。
  useEffect(() => {
    hasMoreRef.current = true
    loadingRef.current = false
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 会话切换时重置翻页瞬态（与 useStudioChat 入口 effect 同一模式）
    setHasMoreHistory(true)
  }, [workspaceId, activeSessionId])

  // 返回是否真正发起了一次拉取（提前返回 = 锁/闩锁/空列表拦截）。
  const loadOlderHistory = useCallback(async (): Promise<boolean> => {
    if (!workspaceId || !activeSessionId) return false
    if (loadingRef.current || !hasMoreRef.current) return false
    // 列表按 seq 升序维护，首行即当前最老游标。
    const oldest = messagesRef.current[0]?.seq
    if (typeof oldest !== 'number') return false
    loadingRef.current = true
    const sessionId = activeSessionId
    try {
      const page = await fetchStudioChatHistoryPage(
        workspaceId,
        sessionId,
        oldest
      )
      // 跨会话竞态（refillMessages 的 activeSessionIdRef 守卫同模式）：
      // 拉取在途时切换了会话，旧会话的旧页不得合入新列表、游标也不推进。
      if (activeSessionIdRef.current !== sessionId) return false
      setMessages((current) =>
        activeSessionIdRef.current !== sessionId
          ? current
          : mergeMessages(current, page.messages)
      )
      if (!page.hasMore) {
        hasMoreRef.current = false
        setHasMoreHistory(false)
      }
      return true
    } catch {
      // 拉取失败不闩锁：游标未推进、hasMore 保持 true，下次滚到顶部重试。
      return false
    } finally {
      loadingRef.current = false
    }
  }, [
    workspaceId,
    activeSessionId,
    activeSessionIdRef,
    messagesRef,
    setMessages,
  ])

  return { hasMoreHistory, loadOlderHistory }
}
