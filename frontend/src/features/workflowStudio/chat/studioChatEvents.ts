import type { QueryClient } from '@tanstack/react-query'
import { invalidateStudioTurnEndQueries } from './studioChatInvalidation'
import {
  calibrateWithRowRefetch,
  hasInFlightToolCall,
  isTerminalStatus,
  type RowRefetchDeps,
} from './studioChatCalibration'
import { handleMissedMessageFrame } from './studioChatMissedRefetch'
import {
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
  /** 会话选择代次（#1237 codex P2-1）：异步回调落地前复核——A→B→A 后
   * sessionId 相同，只有代次能识别早先选择的迟到响应。 */
  sessionGenerationRef: { current: number }
  /** missed 帧点读重试链的去重表（#1289，语义见 RowRefetchDeps）。 */
  missedRetryRef: { current: Map<string, number> }
}

/** SseDeps → RowRefetchDeps（#1236）：按 id 点读校准的消费面。 */
function rowRefetchDeps(deps: SseDeps): RowRefetchDeps {
  return {
    workspaceId: deps.workspaceId,
    sessionId: deps.activeSessionId,
    sessionGenerationRef: deps.sessionGenerationRef,
    missedRetryRef: deps.missedRetryRef,
    messagesRef: deps.messagesRef,
    setMessages: deps.setMessages,
    refillMessages: deps.refillMessages,
    onTerminal: () =>
      invalidateStudioTurnEndQueries(deps.queryClient, deps.workspaceId),
  }
}

/** SSE message 事件的合入：流式残片 upsert（缺 seq 指向未知消息则先按 id
 * 点读、失败回退增量补齐，#1236——窗口外行的更新帧不再被丢弃；补齐静默
 * 携带 terminal 事件时与实时到达的同等触发全量回取与查询失效，#563）。 */
export function handleSseMessageEvent(
  incoming: Partial<ChatMessage> & { id: string },
  deps: SseDeps
): void {
  const { messagesRef, setMessages, queryClient, workspaceId } = deps
  // updater 外判定 missed：updater 在 StrictMode 下会被双调用，副作用放
  // 里面会重复 fetch；补齐失败留待下次事件再试，不产生 unhandled rejection。
  const merged = upsertMessage(messagesRef.current, incoming)
  const missed = merged === null
  setMessages((current) => upsertMessage(current, incoming) ?? current)
  if (missed) handleMissedMessageFrame(rowRefetchDeps(deps), incoming.id)
  if (isTerminalStatus(incoming as ChatMessage)) {
    // #1300：terminal 当轮到达——校准的 turn 截断按合入 incoming 后的快照
    // 计算，否则刚到的新边界不可见，刚终结 turn 的死行被误判在途逐个点读。
    void calibrateWithRowRefetch(
      rowRefetchDeps(deps),
      merged ?? undefined
    ).catch(() => undefined)
    invalidateStudioTurnEndQueries(queryClient, workspaceId)
  }
}

/** SSE 重连自愈（#563）：本地仍挂着未终结的流式 agent text 行或在途
 * tool_call 行（#1228——同为原地更新、seq 不推进的行，断连期间的更新
 * after_seq 取不回）时全量回取校准，窗口外的在途行按 id 单点重取
 * （#1236），随后增量补齐 + 重拉会话快照；补齐携带 terminal 事件时与
 * 实时到达的同等触发查询失效（codex P2——断连期间保存的草稿也要失效）。 */
export function handleSseReconnect(deps: SseDeps): void {
  const { messagesRef, refillMessages, setSession, fetchSession } = deps
  const invalidate = () =>
    invalidateStudioTurnEndQueries(deps.queryClient, deps.workspaceId)
  const checkTerminal = (hasTerminal?: boolean | undefined) => {
    if (hasTerminal) invalidate()
  }
  if (
    streamingTextId(messagesRef.current) !== null ||
    hasInFlightToolCall(messagesRef.current)
  ) {
    void calibrateWithRowRefetch(rowRefetchDeps(deps))
      .then(checkTerminal)
      .catch(() => undefined)
  }
  void refillMessages()
    .then(checkTerminal)
    .catch(() => undefined)
  const generation = deps.sessionGenerationRef.current
  void fetchSession(deps.workspaceId, deps.activeSessionId).then(
    // 落地前复核代次：重连快照在途时切换了会话（含 A→B→A），旧快照丢弃。
    (snapshot) => {
      if (deps.sessionGenerationRef.current === generation) setSession(snapshot)
    },
    () => undefined
  )
}
