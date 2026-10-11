import type { QueryClient } from '@tanstack/react-query'
import { invalidateStudioTurnEndQueries } from './studioChatInvalidation'
import {
  asRecord,
  asText,
  statusEvent,
  streamingTextId,
  TERMINAL,
  upsertMessage,
  type ChatMessage,
} from './studioChatMessages'
import type { StudioChatSessionRecord } from './studioChatApi'

/** terminal 状态行检测（SSE/REST 双路径同源，#563）：任一到达即该 turn
 * 已终结——turn_end/turn_timeout/error/session_closed/session_resumed。 */
export function isTerminalStatus(message: ChatMessage): boolean {
  return message.kind === 'status' && TERMINAL.has(statusEvent(message).event)
}

// ACP tool_call 终态集合，对齐后端 tool_call_commands._FINISHED。
const TOOL_CALL_TERMINAL = new Set(['completed', 'failed'])

/** 存在在途 tool_call 行（#1228 codex P1）：tool_call 合并帧与流式 text 同为
 * 原地更新、seq 不推进，断连期间的更新 after_seq 增量取不回，重连需全量
 * 校准。status 缺失或畸形一律按在途处理——宁可多校准，不漏校准。
 * （唯一消费方是 handleSseReconnect；studioChatMessages.ts 预算已满，故居此。） */
export function hasInFlightToolCall(messages: ChatMessage[]): boolean {
  return messages.some(
    (m) =>
      m.kind === 'tool_call' &&
      !TOOL_CALL_TERMINAL.has(asText(asRecord(m.content)?.status))
  )
}

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
 * 补齐静默携带 terminal 事件时与实时到达的同等触发全量回取与查询失效——
 * 原地更新的流式行 seq 不变、after_seq 取不回，否则截断副本永久定格，
 * #563）。 */
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
        if (hasTerminal) {
          void refillMessages(0).catch(() => undefined)
          invalidateStudioTurnEndQueries(queryClient, workspaceId)
        }
      })
      .catch(() => undefined)
  }
  if (isTerminalStatus(incoming as ChatMessage)) {
    void refillMessages(0).catch(() => undefined)
    invalidateStudioTurnEndQueries(queryClient, workspaceId)
  }
}

/** SSE 重连自愈（#563）：本地仍挂着未终结的流式 agent text 行或在途
 * tool_call 行（#1228——同为原地更新、seq 不推进的行，断连期间的更新
 * after_seq 取不回）时全量回取校准，随后增量补齐 + 重拉会话快照；补齐
 * 携带 terminal 事件时与实时到达的同等触发查询失效（codex P2——断连期间
 * 保存的草稿也要失效）。 */
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
    void refillMessages(0)
      .then(checkTerminal)
      .catch(() => undefined)
  }
  void refillMessages()
    .then(checkTerminal)
    .catch(() => undefined)
  void fetchSession(deps.workspaceId, deps.activeSessionId).then(
    setSession,
    () => undefined
  )
}
