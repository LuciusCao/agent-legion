import { api } from '../../../api/core'
import type { components } from '../../../generated/api'
import {
  asRecord,
  asText,
  statusEvent,
  streamingTextId,
  TERMINAL,
  textContent,
  upsertMessage,
  type ChatMessage,
} from './studioChatMessages'
import type { StudioChatMessageRecord } from './studioChatApi'

/** 原地更新行（流式 agent text / tool_call 合并帧）的按 id 单点重取校准
 *（#1236）：这类行不推进 seq，全量校准 refill(0) 只取最新 500 行窗口——
 * 窗口外的在途行取不回更新；页面初次加载时已在窗口外的行，其后续无 seq
 * 的 SSE 更新帧因未知 id 被丢弃。本模块提供按 id 点读与校准接线：
 * 校准后对仍在途的行逐个重取（最近 terminal 截断，#1290）；点读落地前
 * 校验本地行未被更新的 SSE 快照超越（#1288）；missed 帧（未知 id）的
 * 点读与失败有界重试（#1289）在 studioChatMissedRefetch（复用本模块的
 * fetchAndUpsert）。行分类谓词（isTerminalStatus / hasInFlightToolCall）从
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

/** 最近 terminal 状态之后的当前 turn 行（#1290）：terminal 之前的未终结
 * tool_call 是取消/超时/错误结束的 turn 留下的死行，不再属于任何在途
 * 工作——翻入这些历史后若仍计入，每次 turn 结束/SSE 重连的校准都重复
 * 点读它们。窗口内没有 terminal 时无法判定 turn 边界，保持全窗口扫描
 *（宁多勿漏，与 isInFlightToolCall 的畸形按在途处理同源）。 */
function currentTurnMessages(messages: ChatMessage[]): ChatMessage[] {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (isTerminalStatus(messages[i])) return messages.slice(i + 1)
  }
  return messages
}

/** 当前 turn 存在在途 tool_call 行（#1228 codex P1；#1290 截断到最近
 * terminal 之后）：tool_call 合并帧与流式 text 同为原地更新、seq 不推进，
 * 断连期间的更新 after_seq 增量取不回，重连需全量校准。status 缺失或畸形
 * 一律按在途处理——宁可多校准，不漏校准。 */
export function hasInFlightToolCall(messages: ChatMessage[]): boolean {
  return currentTurnMessages(messages).some(isInFlightToolCall)
}

/** 本地已知的在途原地更新行 id（当前 turn 的未终结 tool_call + 流式 text，
 * #1236；#1290 截断到最近 terminal 之后）：全量校准后逐个按 id 重取——
 * 它们可能在 500 行窗口外，窗口取不回更新。 */
export function inFlightInPlaceRowIds(messages: ChatMessage[]): string[] {
  const ids = currentTurnMessages(messages)
    .filter(isInFlightToolCall)
    .map((m) => m.id)
  const streaming = streamingTextId(messages)
  return streaming === null ? ids : [...ids, streaming]
}

export type RowRefetchDeps = {
  workspaceId: string
  sessionId: string
  /** 会话选择代次（#1237 codex P2-1）：异步落地前复核——A→B→A 后
   * sessionId 相同，只有代次能识别早先选择的迟到响应。 */
  sessionGenerationRef: { current: number }
  /** missed 帧点读重试链的去重表（#1289）：id → 链生效的代次快照（首次
   * 点读点火时捕获，全链沿用，#1297 r2 P1——挂链时现读会拿到切换后的
   * 新代次）。同代次已有待定链时不叠加（重复 missed 帧不倍增重试）；
   * 代次推进后旧条目不再占用（A→B→A 回来可重新挂链），旧链点火时被
   * 代次守卫拦下。 */
  missedRetryRef: { current: Map<string, number> }
  setMessages: (updater: (current: ChatMessage[]) => ChatMessage[]) => void
  refillMessages: (fromSeq?: number) => Promise<boolean | undefined>
  /** terminal 行被补齐时的查询失效（turn_end 连锁，#563）。 */
  onTerminal: () => void
}

/** 点读落地前的新鲜度比较（#1288）：GET 在途期间同一原地行可能已收到更
 * 新的 SSE 快照，无条件 upsert 会把较新的流式文本截短、把已终结的
 * tool call 恢复 in_progress；若那是最后一帧，界面永久停在旧状态。本地
 * 行在任一维度更新（流式文本更长 / tool 状态已终结而点读快照未终结）即
 * 丢弃点读结果；判不出本地更新时以服务端响应为准照常合入。 */
function isLocalRowFresher(local: ChatMessage, fetched: ChatMessage): boolean {
  if (
    local.kind === 'tool_call' &&
    TOOL_CALL_TERMINAL.has(asText(asRecord(local.content)?.status)) &&
    !TOOL_CALL_TERMINAL.has(asText(asRecord(fetched.content)?.status))
  ) {
    return true
  }
  return (
    local.kind === 'text' &&
    textContent(local).length > textContent(fetched).length
  )
}

/** 按 id 点读并 generation + 新鲜度守卫地 upsert；reject 留给调用方决定
 * 回退。generation 缺省在调用点现取；calibrate 链路传入入口快照——点火
 * 时刻晚于入口（隔着一个 refill(0) 的 await），现取会拿到切换后的新代次，
 * 把旧会话的点读行注进新会话列表（复验 finding 1）。
 * 导出给 studioChatMissedRefetch 的 missed 帧重试链复用（#1289）。 */
export function fetchAndUpsert(
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
    deps.setMessages((current) => {
      // #1288：响应读库后本地行可能已被更新的 SSE 快照超越——守卫是纯读，
      // updater 在 StrictMode 双调用下安全。
      const local = current.find((m) => m.id === message.id)
      if (local && isLocalRowFresher(local, message)) return current
      return upsertMessage(current, message) ?? current
    })
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
