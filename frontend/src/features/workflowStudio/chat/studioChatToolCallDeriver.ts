// #1120 PR-2：toolCalls 的增量归并。每个 SSE chunk 都会更换 messages 数组
// 引用，全量 groupToolCalls 会为每张工具卡重建 ToolCallView（含 outputText
// 字符串），MessageItem 的 memo 随之穿透、整个消息列表逐 chunk 重渲染。
// upsertMessage / mergeMessages 保证未触动的消息引用不变，这里按 message.id
// 缓存单条消息的贡献（消息引用不变即复用），再按 toolCallId 比对贡献的
// 引用序列：只有贡献集合真变了才重建该卡的 view。合并语义与 groupToolCalls
// 完全一致（空字段不覆盖既有值、按首现序排列），草稿卡派生
// （extractWorkflowDraft / extractNodeCodeDrafts）拿到的仍是全量 outputText。
import {
  asRecord,
  asText,
  extractOutputText,
  type ChatMessage,
  type ToolCallView,
} from './studioChatMessages'

/** 单条 tool_call 消息对卡片的贡献；空字段在合并时不覆盖（与
 * groupToolCalls 的「后者非空才覆盖」语义对齐）。 */
type ToolCallContribution = {
  toolCallId: string
  title: string
  status: string
  rawInput: Record<string, unknown> | null
  outputText: string
}

function contributionOf(message: ChatMessage): ToolCallContribution | null {
  const content = asRecord(message.content)
  const toolCallId = asText(content?.toolCallId)
  if (!toolCallId) return null
  return {
    toolCallId,
    title: asText(content?.title),
    status: asText(content?.status),
    rawInput: asRecord(content?.rawInput),
    outputText: extractOutputText(content),
  }
}

function mergeContributions(
  toolCallId: string,
  contribs: readonly ToolCallContribution[]
): ToolCallView {
  const view: ToolCallView = {
    toolCallId,
    title: '',
    status: '',
    rawInput: null,
    outputText: '',
  }
  for (const contrib of contribs) {
    if (contrib.title) view.title = contrib.title
    if (contrib.status) view.status = contrib.status
    if (contrib.rawInput) view.rawInput = contrib.rawInput
    if (contrib.outputText) view.outputText = contrib.outputText
  }
  return view
}

function sameContribs(
  cached: readonly ToolCallContribution[],
  next: readonly ToolCallContribution[]
): boolean {
  return cached.length === next.length && cached.every((c, i) => c === next[i])
}

export type ToolCallDeriver = (messages: ChatMessage[]) => ToolCallView[]

export function createToolCallDeriver(): ToolCallDeriver {
  type ContributionEntry = {
    message: ChatMessage
    value: ToolCallContribution | null
  }
  let contributionCache = new Map<string, ContributionEntry>()
  let viewCache = new Map<
    string,
    { contribs: readonly ToolCallContribution[]; view: ToolCallView }
  >()
  let prevResult: ToolCallView[] = []

  return (messages) => {
    const nextContributionCache = new Map<string, ContributionEntry>()
    const grouped = new Map<string, ToolCallContribution[]>()
    const order: string[] = []
    for (const message of messages) {
      if (message.kind !== 'tool_call') continue
      const cached = contributionCache.get(message.id)
      const entry: ContributionEntry =
        cached && cached.message === message
          ? cached
          : { message, value: contributionOf(message) }
      nextContributionCache.set(message.id, entry)
      if (!entry.value) continue
      const { toolCallId } = entry.value
      let bucket = grouped.get(toolCallId)
      if (!bucket) {
        bucket = []
        grouped.set(toolCallId, bucket)
        order.push(toolCallId)
      }
      bucket.push(entry.value)
    }
    const nextViewCache = new Map<
      string,
      { contribs: readonly ToolCallContribution[]; view: ToolCallView }
    >()
    // 结果数组引用稳定化：逐位比对上一轮的 view，全等则复用旧数组——
    // MessageList 的 toolCallById 等下游 memo 以 toolCalls 引用为依赖。
    let stable = prevResult.length === order.length
    const result = order.map((toolCallId, index) => {
      const contribs = grouped.get(toolCallId)!
      const cached = viewCache.get(toolCallId)
      const view =
        cached && sameContribs(cached.contribs, contribs)
          ? cached.view
          : mergeContributions(toolCallId, contribs)
      nextViewCache.set(toolCallId, { contribs, view })
      if (stable && prevResult[index] !== view) stable = false
      return view
    })
    contributionCache = nextContributionCache
    viewCache = nextViewCache
    if (!stable) prevResult = result
    return prevResult
  }
}
