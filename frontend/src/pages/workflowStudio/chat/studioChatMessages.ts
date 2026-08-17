import type { StudioChatMessageRecord } from './studioChatApi'

// 消息 content 是后端透传的 ACP update / 业务字典（契约里是
// { [key: string]: unknown }），这里集中做 unknown-safe 的读取与归并，
// UI 组件只消费本模块输出的视图模型。

export type ChatMessage = StudioChatMessageRecord

export type ToolCallView = {
  toolCallId: string
  title: string
  status: string
  rawInput: Record<string, unknown> | null
  outputText: string
}

export type WorkflowDraftView = {
  yaml: string
  validated: boolean
  compareMeta: string | null
}

export type AgentDefinitionDraftView = {
  toolCallId: string
  agentId: string
  capability: string | null
  runtime: string | null
  skill: string | null
}

export type NodeCodeDraftView = {
  toolCallId: string
  nodeKey: string
}

export type PermissionView = {
  requestId: string
  toolTitle: string
  options: { optionId: string; name: string; kind: string }[]
  resolved: boolean
  decisionText: string | null
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

/** SSE 流式文本更新只携带 id/kind/role/content（无 seq/created_at）：
 * 已存在则合并 content，不存在返回 null 由调用方触发增量拉取补齐。 */
export function upsertMessage(
  messages: ChatMessage[],
  incoming: Partial<ChatMessage> & { id: string }
): ChatMessage[] | null {
  const index = messages.findIndex((message) => message.id === incoming.id)
  if (index < 0) {
    if (typeof incoming.seq !== 'number') return null
    const full = incoming as ChatMessage
    const next = [...messages, full]
    next.sort((a, b) => a.seq - b.seq)
    return next
  }
  const current = messages[index]
  const merged: ChatMessage = {
    ...current,
    ...incoming,
    content: { ...current.content, ...(asRecord(incoming.content) ?? {}) },
    seq: current.seq,
    created_at: current.created_at,
  }
  const next = [...messages]
  next[index] = merged
  return next
}

export function maxSeq(messages: ChatMessage[]): number {
  return messages.reduce((max, message) => Math.max(max, message.seq), 0)
}

/** tool_call / tool_call_update 是按 ACP update 逐条落库的独立消息，
 * 前端按 toolCallId 归并成一张工具卡片（保留首次出现顺序）。 */
export function groupToolCalls(messages: ChatMessage[]): ToolCallView[] {
  const byId = new Map<string, ToolCallView>()
  const order: string[] = []
  for (const message of messages) {
    if (message.kind !== 'tool_call') continue
    const content = asRecord(message.content)
    const toolCallId = asText(content?.toolCallId)
    if (!toolCallId) continue
    let view = byId.get(toolCallId)
    if (!view) {
      view = {
        toolCallId,
        title: '',
        status: '',
        rawInput: null,
        outputText: '',
      }
      byId.set(toolCallId, view)
      order.push(toolCallId)
    }
    const title = asText(content?.title)
    if (title) view.title = title
    const status = asText(content?.status)
    if (status) view.status = status
    const rawInput = asRecord(content?.rawInput)
    if (rawInput) view.rawInput = rawInput
    const output = extractOutputText(content)
    if (output) view.outputText = output
  }
  return order.map((id) => byId.get(id)!)
}

function extractOutputText(content: Record<string, unknown> | null): string {
  if (!content) return ''
  const rawOutput = content.rawOutput
  const fromRaw = textFromBlocks(rawOutput)
  if (fromRaw) return fromRaw
  return textFromBlocks(content.content)
}

function textFromBlocks(value: unknown): string {
  if (typeof value === 'string') return value
  const record = asRecord(value)
  if (record) {
    const inner = record.content
    if (Array.isArray(inner)) return textFromBlocks(inner)
    return ''
  }
  if (Array.isArray(value)) {
    return value
      .map((block) => asText(asRecord(block)?.text))
      .filter(Boolean)
      .join('\n')
  }
  return ''
}

/** 从工具输出文本里解析第一个 JSON 对象（MCP 工具返回的是响应体文本）。 */
export function parseFirstJson(text: string): Record<string, unknown> | null {
  const trimmed = text.trim()
  if (!trimmed.startsWith('{')) return null
  try {
    return asRecord(JSON.parse(trimmed))
  } catch {
    const match = trimmed.match(/\{[\s\S]*\}/)
    if (!match) return null
    try {
      return asRecord(JSON.parse(match[0]))
    } catch {
      return null
    }
  }
}

function toolNameMatches(call: ToolCallView, name: string): boolean {
  return call.title.toLowerCase().includes(name)
}

function listCount(value: unknown): number {
  return Array.isArray(value) ? value.length : 0
}

/** 最近一次 validate_workflow / compare_workflow 携带的 definition_yaml
 * 即 agent 产出的 workflow 草稿；校验通过与对比摘要取自对应工具输出。 */
export function extractWorkflowDraft(
  calls: ToolCallView[]
): WorkflowDraftView | null {
  let draft: WorkflowDraftView | null = null
  for (const call of calls) {
    const yaml = asText(call.rawInput?.definition_yaml)
    if (!yaml) continue
    if (
      !toolNameMatches(call, 'validate_workflow') &&
      !toolNameMatches(call, 'compare_workflow')
    ) {
      continue
    }
    if (!draft || draft.yaml !== yaml) {
      draft = { yaml, validated: false, compareMeta: null }
    }
    if (toolNameMatches(call, 'validate_workflow')) {
      const parsed = parseFirstJson(call.outputText)
      if (parsed?.valid === true) draft.validated = true
    }
    if (toolNameMatches(call, 'compare_workflow')) {
      draft.compareMeta = compareMetaText(call.outputText) ?? draft.compareMeta
      const parsed = parseFirstJson(call.outputText)
      if (parsed?.valid === true) draft.validated = true
    }
  }
  return draft
}

function compareMetaText(outputText: string): string | null {
  const parsed = parseFirstJson(outputText)
  const summary = asRecord(parsed?.summary)
  if (!parsed || parsed.valid !== true || !summary) return null
  const added = (key: string) =>
    (Array.isArray(summary[key]) ? summary[key] : []).filter(
      (change) => asRecord(change)?.type === 'added'
    ).length
  const nodes = added('node_changes')
  const edges = added('edge_changes')
  const parts: string[] = []
  if (nodes) parts.push(`新增 ${nodes} 个节点`)
  if (edges) parts.push(`新增 ${edges} 条边`)
  const modified = listCount(summary.node_changes) - nodes
  if (modified > 0) parts.push(`修改 ${modified} 个节点`)
  return parts.length > 0 ? parts.join(' · ') : '定义级变更'
}

export function extractAgentDefinitionDrafts(
  calls: ToolCallView[]
): AgentDefinitionDraftView[] {
  const drafts: AgentDefinitionDraftView[] = []
  for (const call of calls) {
    if (!toolNameMatches(call, 'save_agent_definition_draft')) continue
    const agentId = asText(call.rawInput?.agent_id)
    if (!agentId) continue
    drafts.push({
      toolCallId: call.toolCallId,
      agentId,
      capability: asText(call.rawInput?.capability) || null,
      runtime: asText(call.rawInput?.runtime) || null,
      skill: asText(call.rawInput?.skill) || null,
    })
  }
  return drafts
}

export function extractNodeCodeDrafts(
  calls: ToolCallView[]
): NodeCodeDraftView[] {
  const drafts: NodeCodeDraftView[] = []
  for (const call of calls) {
    if (!toolNameMatches(call, 'save_node_code_draft')) continue
    const nodeKey = asText(call.rawInput?.node_key)
    if (!nodeKey) continue
    drafts.push({ toolCallId: call.toolCallId, nodeKey })
  }
  return drafts
}

/** 待应答的权限请求：pending 消息存在且没有同 request_id 的 resolved 消息。 */
export function buildPermissionViews(
  messages: ChatMessage[]
): PermissionView[] {
  const resolvedBy = new Map<string, Record<string, unknown>>()
  for (const message of messages) {
    if (message.kind !== 'permission') continue
    const content = asRecord(message.content)
    if (content?.status !== 'resolved') continue
    const requestId = asText(content.request_id)
    if (requestId) resolvedBy.set(requestId, content)
  }
  const views: PermissionView[] = []
  for (const message of messages) {
    if (message.kind !== 'permission') continue
    const content = asRecord(message.content)
    if (content?.status !== 'pending') continue
    const requestId = asText(content.request_id)
    if (!requestId) continue
    const toolCall = asRecord(content.tool_call)
    const options = Array.isArray(content.options) ? content.options : []
    const resolved = resolvedBy.get(requestId)
    views.push({
      requestId,
      toolTitle: asText(toolCall?.title) || '工具调用',
      options: options
        .map((option) => asRecord(option))
        .filter((option): option is Record<string, unknown> => option !== null)
        .map((option) => ({
          optionId: asText(option.optionId),
          name: asText(option.name) || asText(option.optionId),
          kind: asText(option.kind),
        }))
        .filter((option) => option.optionId),
      resolved: resolved !== undefined,
      decisionText: resolved ? decisionText(resolved) : null,
    })
  }
  return views
}

/** 自动批准（平台工具/全部允许）的 permission 消息没有 pending 前置消息，
 * 渲染成一行系统提示时取这段文字。 */
export function permissionResolutionText(message: ChatMessage): string | null {
  const content = asRecord(message.content)
  if (content?.status !== 'resolved') return null
  const toolCall = asRecord(content.tool_call)
  const title = asText(toolCall?.title)
  const text = decisionText(content)
  return title ? `${text}：${title}` : text
}

export type PlanEntry = { content: string; status: string }

export function planEntries(message: ChatMessage): PlanEntry[] {
  const content = asRecord(message.content)
  const entries = Array.isArray(content?.entries) ? content.entries : []
  return entries
    .map((entry) => asRecord(entry))
    .filter((entry): entry is Record<string, unknown> => entry !== null)
    .map((entry) => ({
      content: asText(entry.content),
      status: asText(entry.status),
    }))
    .filter((entry) => entry.content)
}

export function statusEvent(message: ChatMessage): {
  event: string
  detail: string
} {
  const content = asRecord(message.content)
  return {
    event: asText(content?.event),
    detail: asText(content?.detail),
  }
}

export function textContent(message: ChatMessage): string {
  return asText(asRecord(message.content)?.text)
}

const TERMINAL = new Set(['turn_end', 'error', 'session_closed'])

/** 仍在流式聚合的 agent text 消息 id：从尾部扫描，先撞到 turn 终止事件
 * （turn_end/error/session_closed）则全部完成返回 null，先撞到 agent
 * text 则该条仍在流式。 */
export function streamingTextId(messages: ChatMessage[]): string | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i]
    if (m.kind === 'status' && TERMINAL.has(statusEvent(m).event)) return null
    if (m.kind === 'text' && m.role === 'agent') return m.id
  }
  return null
}

function decisionText(resolved: Record<string, unknown>): string {
  const decision = asRecord(resolved.decision)
  if (!decision) return '已处理'
  if (decision.deny === true) return '已拒绝'
  const via = asText(decision.via)
  if (via === 'auto_approved') return '已自动允许（平台工具）'
  if (via === 'auto_read_only') return '已自动允许（只读工具）'
  if (via === 'allow_all') return '已自动允许（本次全部允许）'
  return '已允许'
}

/** 消息列表的派生视图集合（hook 里单次 useMemo 消费，避免每个视图一条
 * memo 链）。 */
export function deriveChatViews(messages: ChatMessage[]) {
  const toolCalls = groupToolCalls(messages)
  return {
    toolCalls,
    workflowDraft: extractWorkflowDraft(toolCalls),
    agentDrafts: extractAgentDefinitionDrafts(toolCalls),
    nodeDrafts: extractNodeCodeDrafts(toolCalls),
    permissions: buildPermissionViews(messages),
  }
}
