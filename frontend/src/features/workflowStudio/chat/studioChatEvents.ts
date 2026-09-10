import type { QueryClient } from '@tanstack/react-query'
import { invalidateStudioTurnEndQueries } from './studioChatInvalidation'
import {
  statusEvent,
  streamingTextId,
  upsertMessage,
  type ChatMessage,
} from './studioChatMessages'
import type { StudioChatSessionRecord } from './studioChatApi'

export type SsePayload = {
  type?: string
  message?: Partial<ChatMessage> & { id: string }
  session?: StudioChatSessionRecord
}

type SseDeps = {
  workspaceId: string
  messagesRef: { current: ChatMessage[] }
  queryClient: QueryClient
  setMessages: (updater: (current: ChatMessage[]) => ChatMessage[]) => void
  setSession: (session: StudioChatSessionRecord) => void
  refillMessages: (fromSeq?: number) => Promise<boolean | undefined>
  fetchSession: (
    workspaceId: string,
    sessionId: string
  ) => Promise<StudioChatSessionRecord>
  activeSessionId: string
}

/** SSE message 事件的合入：流式残片 upsert（缺 seq 指向未知消息则增量补齐，
 * 补齐静默携带 turn_end 时与实时到达的同等触发全量回取——原地更新的流式
 * 行 seq 不变、after_seq 取不回，否则截断副本永久定格，#563）。 */
export function handleSseMessageEvent(
  incoming: Partial<ChatMessage> & { id: string },
  deps: SseDeps
): void {
  const { messagesRef, setMessages, refillMessages, queryClient, workspaceId } =
    deps
  // updater 外判定 missed：updater 在 StrictMode 下会被双调用，副作用放
  // 里面会重复 fetch；补齐失败留待下次事件再试，不产生 unhandled rejection。
  const missed = upsertMessage(messagesRef.current, incoming) === null
  setMessages((current) => upsertMessage(current, incoming) ?? current)
  if (missed) {
    void refillMessages()
      .then((hasTerminal) => {
        if (hasTerminal) void refillMessages(0).catch(() => undefined)
      })
      .catch(() => undefined)
  }
  if (statusEvent(incoming as ChatMessage).event === 'turn_end') {
    void refillMessages(0).catch(() => undefined)
    invalidateStudioTurnEndQueries(queryClient, workspaceId)
  }
}

/** SSE 重连（status=open）的自愈：本地仍挂着未终结的流式 agent text 行
 * （turn 在断连期间结束、turn_end 只能经 after_seq 补齐静默合入）时直接
 * 全量回取一次校准；随后照常增量补齐 + 重拉会话快照（断连期间的状态
 * 翻转不补发 SSE，不重拉则本地 status 滞留 running）。 */
export function handleSseReconnect(deps: SseDeps): void {
  const {
    messagesRef,
    refillMessages,
    workspaceId,
    activeSessionId,
    setSession,
    fetchSession,
  } = deps
  if (streamingTextId(messagesRef.current) !== null) {
    void refillMessages(0).catch(() => undefined)
  }
  void refillMessages().catch(() => undefined)
  void fetchSession(workspaceId, activeSessionId).then(
    setSession,
    () => undefined
  )
}
