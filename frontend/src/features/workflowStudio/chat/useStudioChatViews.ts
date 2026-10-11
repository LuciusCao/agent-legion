// #1120 PR-2：deriveChatViews 的引用稳定化包装（MessageItem memo 修复）。
// toolCalls 走 createToolCallDeriver 增量归并——未触动消息的 ToolCallView
// 保持 Object.is 相等；workflowDraft/nodeDrafts 是跨卡全序扫描语义
// （extractWorkflowDraft / keepLatestPerEntity），输入只有少量 ToolCallView、
// 成本低，保持全量派生，但输出做结构等价稳定化：内容没变就复用旧引用，
// 否则这两个全行 prop 每次派生都换新对象，MessageItem 的 memo 照样穿透。
import { useMemo, useState } from 'react'
import {
  buildPermissionViews,
  extractNodeCodeDrafts,
  extractWorkflowDraft,
  type ChatMessage,
  type NodeCodeDraftView,
  type WorkflowDraftView,
} from './studioChatMessages'
import { createToolCallDeriver } from './studioChatToolCallDeriver'

type DraftViews = {
  workflowDraft: WorkflowDraftView | null
  nodeDrafts: NodeCodeDraftView[]
}

function sameWorkflowDraft(
  a: WorkflowDraftView | null,
  b: WorkflowDraftView | null
): boolean {
  return (
    a === b ||
    (a !== null &&
      b !== null &&
      a.yaml === b.yaml &&
      a.validated === b.validated &&
      a.compareMeta === b.compareMeta &&
      a.draftHash === b.draftHash)
  )
}

function sameNodeDrafts(
  a: readonly NodeCodeDraftView[],
  b: readonly NodeCodeDraftView[]
): boolean {
  return (
    a.length === b.length &&
    a.every(
      (draft, i) =>
        draft.toolCallId === b[i].toolCallId &&
        draft.nodeKey === b[i].nodeKey &&
        draft.status === b[i].status &&
        draft.draftHash === b[i].draftHash &&
        draft.saveFailed === b[i].saveFailed
    )
  )
}

/** 结构等价稳定化：派生内容与上一轮一致时复用旧引用。闭包缓存是纯记忆
 * 化，StrictMode 双渲染下幂等。 */
function createDraftStabilizer() {
  let prev: DraftViews | null = null
  return (next: DraftViews): DraftViews => {
    const result =
      prev &&
      sameWorkflowDraft(prev.workflowDraft, next.workflowDraft) &&
      sameNodeDrafts(prev.nodeDrafts, next.nodeDrafts)
        ? prev
        : next
    prev = result
    return result
  }
}

export function useStudioChatViews(messages: ChatMessage[]) {
  // useState 惰性初始化只用于持有跨渲染的稳定实例；两个 deriver 的内部缓存
  // 均为幂等记忆化。
  const [deriveToolCalls] = useState(() => createToolCallDeriver())
  const [stabilizeDrafts] = useState(() => createDraftStabilizer())
  const toolCalls = deriveToolCalls(messages)
  const drafts = useMemo(
    () =>
      stabilizeDrafts({
        workflowDraft: extractWorkflowDraft(toolCalls),
        nodeDrafts: extractNodeCodeDrafts(toolCalls),
      }),
    [toolCalls, stabilizeDrafts]
  )
  const permissions = useMemo(() => buildPermissionViews(messages), [messages])
  return {
    toolCalls,
    workflowDraft: drafts.workflowDraft,
    nodeDrafts: drafts.nodeDrafts,
    permissions,
  }
}
