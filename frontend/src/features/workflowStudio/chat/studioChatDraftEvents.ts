import {
  asRecord,
  asText,
  type ChatMessage,
  type NodeCodeDraftView,
  type ToolCallView,
  type WorkflowDraftView,
} from './studioChatMessages'

/* #1123：合成 draft_saved 事件（kind='draft' 消息）与 tool_call 派生证据的
 * 合并。子代理（Agent/AgentSwarm）的 MCP 保存不会被 kimi 转发为 ACP
 * session/update，时间线上没有对应 tool_call 消息；后端在保存成功路径补写
 * 合成事件（server/app/studio_chat/draft_events.py）。两个来源按实体 +
 * seq 合并、最新者胜：主 agent 路径下 ACP 的 completed 更新恒在合成事件之后
 * （合成事件写在路由返回之前），所以同一保存只出一张卡、锚点仍在工具卡上，
 * 合成事件只补子代理的缺口。
 *
 * 合成事件只在保存成功后发出，因此它派生的视图恒为 completed/saveFailed=false，
 * 并携带保存响应的身份 hash（code_hash / definition_hash）——发布 CAS 与
 * stale hint 的语义与 tool-call 来源一致。 */

export type DraftSavedEvent = {
  messageId: string
  seq: number
  draftType: 'workflow' | 'node_code'
  nodeKey: string | null
  codeHash: string | null
  definitionYaml: string | null
  definitionHash: string | null
}

export type MergedNodeCodeDraft = NodeCodeDraftView & {
  /** 证据时刻：tool-call 来源取最后一条贡献消息的 seq，合成事件取自身 seq。 */
  seq: number
  /** 合成事件的锚点消息（卡片挂在该消息位置）；tool-call 来源为 null。 */
  anchorMessageId: string | null
}

export type MergedWorkflowDraft = WorkflowDraftView & {
  anchorToolCallId: string | null
  anchorMessageId: string | null
}

export function extractDraftSavedEvents(
  messages: ChatMessage[]
): DraftSavedEvent[] {
  const events: DraftSavedEvent[] = []
  for (const message of messages) {
    if (message.kind !== 'draft') continue
    const content = asRecord(message.content)
    if (content?.event !== 'draft_saved') continue
    const draftType = asText(content.draft_type)
    if (draftType !== 'workflow' && draftType !== 'node_code') continue
    events.push({
      messageId: message.id,
      seq: message.seq,
      draftType,
      nodeKey: asText(content.node_key) || null,
      codeHash: asText(content.code_hash) || null,
      definitionYaml: asText(content.definition_yaml) || null,
      definitionHash: asText(content.definition_hash) || null,
    })
  }
  return events
}

/** toolCallId → 最后一条贡献消息的 seq（合并排序的证据时刻）。 */
function toolCallLastSeq(messages: ChatMessage[]): Map<string, number> {
  const lastSeq = new Map<string, number>()
  for (const message of messages) {
    if (message.kind !== 'tool_call') continue
    const id = asText(asRecord(message.content)?.toolCallId)
    if (id) lastSeq.set(id, Math.max(lastSeq.get(id) ?? 0, message.seq))
  }
  return lastSeq
}

/** workflow 草稿卡的锚定工具调用：最后一个携带该 yaml 的 validate/compare
 * 调用——草稿卡挂在它后面（#1120 codex P1 轮自 StudioChatMessageList 移出，
 * #1123 合并后由 mergeWorkflowDraft 消费）。 */
export function draftAnchorToolCallId(
  toolCalls: ToolCallView[],
  draft: WorkflowDraftView | null
): string | null {
  if (!draft) return null
  const anchor = toolCalls
    .filter(
      (call) =>
        call.rawInput?.definition_yaml === draft.yaml &&
        (call.title.toLowerCase().includes('validate_workflow') ||
          call.title.toLowerCase().includes('compare_workflow'))
    )
    .slice(-1)[0]
  return anchor?.toolCallId ?? null
}

/** 节点代码草稿按实体合并两个来源：同实体取证据时刻最新的一份（主 agent
 * 路径下 tool call 的 completed 更新恒晚于合成事件，卡片锚点保持现状）。
 * #1240 codex P2-b：合成事件只在「它宣告的草稿仍是当前草稿」时落库
 * （后端 INSERT 内原子校验 code_hash/updated_at），迟到的停滞写者事件
 * 根本不会出现——seq 顺序因此等于权威草稿写入顺序。 */
export function mergeNodeCodeDrafts(
  base: NodeCodeDraftView[],
  messages: ChatMessage[]
): MergedNodeCodeDraft[] {
  const lastSeq = toolCallLastSeq(messages)
  const byNode = new Map<string, MergedNodeCodeDraft>()
  for (const draft of base) {
    byNode.set(draft.nodeKey, {
      ...draft,
      seq: lastSeq.get(draft.toolCallId) ?? 0,
      anchorMessageId: null,
    })
  }
  for (const event of extractDraftSavedEvents(messages)) {
    if (event.draftType !== 'node_code' || !event.nodeKey) continue
    const current = byNode.get(event.nodeKey)
    if (current && current.seq >= event.seq) continue
    byNode.set(event.nodeKey, {
      toolCallId: '',
      nodeKey: event.nodeKey,
      status: 'completed',
      draftHash: event.codeHash,
      saveFailed: false,
      seq: event.seq,
      anchorMessageId: event.messageId,
    })
  }
  return [...byNode.values()].sort((a, b) => a.seq - b.seq)
}

/** Workflow 草稿合并：先按现状从 validate/compare 派生（锚点 = 最后一个携带
 * 该 yaml 的校验/对比调用），再用更晚到达的合成保存事件覆盖——合成事件携带
 * 的是服务端已落库的草稿内容（yaml + definition_hash），且只在落库时仍是
 * 当前草稿才写入（#1240 codex P2-b 的 updated_at 代次闸），比「agent 曾校验
 * 过的输入」更权威。yaml 相同的合成事件不覆盖：内容一致，保留已有的
 * validated/compareMeta 与工具卡锚点。 */
export function mergeWorkflowDraft(
  base: WorkflowDraftView | null,
  calls: ToolCallView[],
  messages: ChatMessage[]
): MergedWorkflowDraft | null {
  const anchorToolCallId = draftAnchorToolCallId(calls, base)
  const baseSeq =
    (anchorToolCallId && toolCallLastSeq(messages).get(anchorToolCallId)) || -1
  let latestSave: DraftSavedEvent | null = null
  for (const event of extractDraftSavedEvents(messages)) {
    if (event.draftType === 'workflow' && event.definitionYaml)
      latestSave = event
  }
  if (
    latestSave &&
    latestSave.definitionYaml &&
    latestSave.seq > baseSeq &&
    latestSave.definitionYaml !== base?.yaml
  ) {
    return {
      yaml: latestSave.definitionYaml,
      validated: false,
      compareMeta: null,
      draftHash: latestSave.definitionHash,
      anchorToolCallId: null,
      anchorMessageId: latestSave.messageId,
    }
  }
  if (!base) return null
  return { ...base, anchorToolCallId, anchorMessageId: null }
}
