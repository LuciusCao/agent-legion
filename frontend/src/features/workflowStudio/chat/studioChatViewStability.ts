// #1120（codex P1）：派生视图的结构等价稳定化。useStudioChatViews 每次
// 派生都会产出新对象；workflowDraft / nodeDrafts / permissions 是
// MessageItem 的全行 prop，内容没变却换新引用会穿透 memo、让每个流式
// chunk 重渲染整个可见列表。这里的比较器按值判等，stabilizer 在内容一致
// 时复用上一轮引用。
import type {
  NodeCodeDraftView,
  PermissionView,
  WorkflowDraftView,
} from './studioChatMessages'

export type DraftViews = {
  workflowDraft: WorkflowDraftView | null
  nodeDrafts: NodeCodeDraftView[]
}

export function sameWorkflowDraft(
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

export function sameNodeDrafts(
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

export function sameDraftViews(a: DraftViews, b: DraftViews): boolean {
  return (
    sameWorkflowDraft(a.workflowDraft, b.workflowDraft) &&
    sameNodeDrafts(a.nodeDrafts, b.nodeDrafts)
  )
}

export function samePermissionView(
  a: PermissionView,
  b: PermissionView
): boolean {
  return (
    a.requestId === b.requestId &&
    a.toolTitle === b.toolTitle &&
    a.command === b.command &&
    a.resolved === b.resolved &&
    a.decisionText === b.decisionText &&
    a.options.length === b.options.length &&
    a.options.every(
      (option, i) =>
        option.optionId === b.options[i].optionId &&
        option.name === b.options[i].name &&
        option.kind === b.options[i].kind
    )
  )
}

/** 按位比较：permission 视图按消息序排列，流式期间位置稳定；位置错动的
 * 极少数场景（补页插入）退化为换新引用，正确性不受影响。 */
export function samePermissionViews(
  a: readonly PermissionView[],
  b: readonly PermissionView[]
): boolean {
  return (
    a.length === b.length &&
    a.every((view, i) => samePermissionView(view, b[i]))
  )
}

/** 引用稳定化：next 与上一轮内容等价（same 判等）时返回上一轮引用。
 * 闭包缓存是纯记忆化，StrictMode 双渲染下幂等。 */
export function createRefStabilizer<T>(
  same: (a: T, b: T) => boolean
): (next: T) => T {
  let prev: T | null = null
  return (next) => {
    const result = prev !== null && same(prev, next) ? prev : next
    prev = result
    return result
  }
}
