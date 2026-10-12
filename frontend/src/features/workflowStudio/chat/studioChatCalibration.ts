import { api } from '../../../api/core'
import type { components } from '../../../generated/api'
import {
  asRecord,
  asText,
  statusEvent,
  streamingTextId,
  TERMINAL,
  upsertMessage,
  type ChatMessage,
} from './studioChatMessages'
import type { StudioChatMessageRecord } from './studioChatApi'

/** 原地更新行（流式 agent text / tool_call 合并帧）的按 id 单点重取校准
 *（#1236）：这类行不推进 seq，全量校准 refill(0) 只取最新 500 行窗口——
 * 窗口外的在途行取不回更新；页面初次加载时已在窗口外的行，其后续无 seq
 * 的 SSE 更新帧因未知 id 被丢弃。本模块提供按 id 点读与两条接线：
 * 校准后对仍在途的行逐个重取；missed 帧（未知 id）先按 id 点读、失败再
 * 回退增量补齐。行分类谓词（isTerminalStatus / hasInFlightToolCall）从
 * studioChatEvents 迁居于此——events 与本模块互相消费，谓词必须单向依赖。 */

type MessageResponse = components['schemas']['StudioChatMessageResponse']

/** 单条消息点读（#1236）：校准窗口外的原地更新行按 id 重取。 */
export function fetchStudioChatMessage(
  workspaceId: string,
  sessionId: string,
  messageId: string
): Promise<StudioChatMessageRecord> {
  return api<MessageResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/studio-chat/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}`
  ).then((response) => response.message)
}

/** terminal 状态行检测（SSE/REST 双路径同源，#563）：任一到达即该 turn
 * 已终结——turn_end/turn_timeout/error/session_closed/session_resumed。 */
export function isTerminalStatus(message: ChatMessage): boolean {
  return message.kind === 'status' && TERMINAL.has(statusEvent(message).event)
}

// ACP tool_call 终态集合，对齐后端 tool_call_commands._FINISHED。
const TOOL_CALL_TERMINAL = new Set(['completed', 'failed'])

function isInFlightToolCall(message: ChatMessage): boolean {
  return (
    message.kind === 'tool_call' &&
    !TOOL_CALL_TERMINAL.has(asText(asRecord(message.content)?.status))
  )
}

/** 存在在途 tool_call 行（#1228 codex P1）：tool_call 合并帧与流式 text 同为
 * 原地更新、seq 不推进，断连期间的更新 after_seq 增量取不回，重连需全量
 * 校准。status 缺失或畸形一律按在途处理——宁可多校准，不漏校准。 */
export function hasInFlightToolCall(messages: ChatMessage[]): boolean {
  return messages.some(isInFlightToolCall)
}

/** 本地已知的在途原地更新行 id（流式 text + 未终结 tool_call，#1236）：
 * 全量校准后逐个按 id 重取——它们可能在 500 行窗口外，窗口取不回更新。 */
export function inFlightInPlaceRowIds(messages: ChatMessage[]): string[] {
  const ids = messages.filter(isInFlightToolCall).map((m) => m.id)
  const streaming = streamingTextId(messages)
  return streaming === null ? ids : [...ids, streaming]
}

export type RowRefetchDeps = {
  workspaceId: string
  sessionId: string
  /** 会话选择代次（#1237 codex P2-1）：异步落地前复核——A→B→A 后
   * sessionId 相同，只有代次能识别早先选择的迟到响应。 */
  sessionGenerationRef: { current: number }
  setMessages: (updater: (current: ChatMessage[]) => ChatMessage[]) => void
  refillMessages: (fromSeq?: number) => Promise<boolean | undefined>
  /** terminal 行被补齐时的查询失效（turn_end 连锁，#563）。 */
  onTerminal: () => void
}

/** 按 id 点读并 generation 守卫地 upsert；reject 留给调用方决定回退。
 * generation 缺省在调用点现取；calibrate 链路传入入口快照——点火时刻晚于
 * 入口（隔着一个 refill(0) 的 await），现取会拿到切换后的新代次，把旧会话
 * 的点读行注进新会话列表（复验 finding 1）。 */
function fetchAndUpsert(
  deps: RowRefetchDeps,
  messageId: string,
  generation = deps.sessionGenerationRef.current
): Promise<void> {
  return fetchStudioChatMessage(
    deps.workspaceId,
    deps.sessionId,
    messageId
  ).then((message) => {
    if (deps.sessionGenerationRef.current !== generation) return
    deps.setMessages((current) => upsertMessage(current, message) ?? current)
  })
}

/** missed 帧（无 seq 指向未知 id 的原地更新帧，#1236）：先按 id 点读——
 * 该行在校准窗口外时增量补齐永远取不回它。点读成功后该行变为已知，后续
 * 更新帧照常 upsert；点读失败（404/网络）回退到旧的增量补齐路径。
 * 无 terminal 连锁：terminal 行必带 seq 以新行到达（upsert 即知），
 * 不经过 missed 路径。 */
export function handleMissedMessageFrame(
  deps: RowRefetchDeps,
  messageId: string
): void {
  void fetchAndUpsert(deps, messageId).then(undefined, () => {
    void deps
      .refillMessages()
      .then((hasTerminal) => {
        if (hasTerminal) {
          void deps.refillMessages(0).catch(() => undefined)
          deps.onTerminal()
        }
      })
      .catch(() => undefined)
  })
}

/** 全量校准 + 窗口外在途行补取（#1236）：refill(0) 只覆盖最新 500 行窗口，
 * 本地已知但在窗口外的在途原地更新行随后按 id 逐个重取。返回 refill 的
 * terminal 判定（调用方据此连锁查询失效）。 */
export function calibrateWithRowRefetch(
  deps: RowRefetchDeps & { messagesRef: { current: ChatMessage[] } }
): Promise<boolean | undefined> {
  // 先快照在途行：refill 的合入是异步的，校准完成后再读 messagesRef 可能
  // 已把窗口外行更新成窗口内的陈旧副本——快照的是「需要校准的身份」。
  const ids = inFlightInPlaceRowIds(deps.messagesRef.current)
  // 代次钉在入口（复验 finding 1）：refill 在途期间切换会话时，落地守卫
  // 拦下 refill 自身的合入，点读点火也必须在同一代次判定下被拦——否则
  // 旧会话的点读行会插入新会话的消息列表。
  const generation = deps.sessionGenerationRef.current
  return deps.refillMessages(0).then((hasTerminal) => {
    if (deps.sessionGenerationRef.current !== generation) return hasTerminal
    for (const id of ids)
      void fetchAndUpsert(deps, id, generation).catch(() => undefined)
    return hasTerminal
  })
}
