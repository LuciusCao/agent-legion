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
 * 状态机：loadingRef 请求 token 防重入（加载中不重复拉，finally 只许
 * 持有者释放）；hasMore=false 闩锁到顶终止；
 * 失败不推进游标也不闩锁 hasMore，下次滚到顶部自然重试。 */
export function useStudioChatHistory({
  workspaceId,
  activeSessionId,
  activeSessionIdRef,
  sessionGenerationRef,
  messagesRef,
  setMessages,
}: {
  workspaceId: string | undefined
  activeSessionId: string | null
  activeSessionIdRef: MutableRefObject<string | null>
  sessionGenerationRef: MutableRefObject<number>
  messagesRef: MutableRefObject<ChatMessage[]>
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>
}) {
  const [hasMoreHistory, setHasMoreHistory] = useState(true)
  const hasMoreRef = useRef(true)
  // 加载中锁绑定请求 token（#1237 codex P2-2）：finally 只许持有者释放——
  // 切换会话的复位清锁后，旧会话迟到请求的 finally 不得清掉新会话在途
  // 请求的锁（否则下一次到顶会以同游标重复发请求）。
  const loadingRef = useRef<number | null>(null)
  const requestTokenRef = useRef(0)

  // 切换会话/workspace：游标与到顶闩锁复位（消息列表由入口 effect 清空）。
  useEffect(() => {
    hasMoreRef.current = true
    loadingRef.current = null
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 会话切换时重置翻页瞬态（与 useStudioChat 入口 effect 同一模式）
    setHasMoreHistory(true)
  }, [workspaceId, activeSessionId])

  // 返回是否真正发起了一次拉取（提前返回 = 锁/闩锁/空列表拦截）。
  // oldestSeqHint：触发方当前渲染快照的最老游标（#1237 codex 八轮 P2）——
  // 子组件 effect 触发的补拉不能依赖 messagesRef：它由父组件 passive
  // effect 同步，子 effect 先跑时会读到上一轮的滞后值。直接调用（事件
  // 回调等 commit 后时机）缺省回退 messagesRef。
  const loadOlderHistory = useCallback(
    async (oldestSeqHint?: number): Promise<boolean> => {
      if (!workspaceId || !activeSessionId) return false
      if (loadingRef.current !== null || !hasMoreRef.current) return false
      // 列表按 seq 升序维护，首行即当前最老游标。
      const oldest = oldestSeqHint ?? messagesRef.current[0]?.seq
      if (typeof oldest !== 'number') return false
      const token = ++requestTokenRef.current
      loadingRef.current = token
      const sessionId = activeSessionId
      const generation = sessionGenerationRef.current
      try {
        const page = await fetchStudioChatHistoryPage(
          workspaceId,
          sessionId,
          oldest
        )
        // 跨会话竞态（refillMessages 的守卫同模式，#1237 codex P2-1 升级为
        // 会话 + 选择代次双检）：拉取在途时切换了会话——包括 A→B→A 重新选中
        // 同一会话——早先代次的旧页不得合入新列表、不得闩锁 hasMore（否则
        // 重新进入的首屏窗口覆盖旧页后永远无法继续分页）、游标也不推进。
        const current = () =>
          activeSessionIdRef.current === sessionId &&
          sessionGenerationRef.current === generation
        if (!current()) return false
        setMessages((currentMessages) =>
          current()
            ? mergeMessages(currentMessages, page.messages)
            : currentMessages
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
        if (loadingRef.current === token) loadingRef.current = null
      }
    },
    [
      workspaceId,
      activeSessionId,
      activeSessionIdRef,
      sessionGenerationRef,
      messagesRef,
      setMessages,
    ]
  )

  return { hasMoreHistory, loadOlderHistory }
}
