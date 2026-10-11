// #1120 PR-2：deriveChatViews 的引用稳定化包装（MessageItem memo 修复）。
// toolCalls 走 createToolCallDeriver 增量归并——未触动消息的 ToolCallView
// 保持 Object.is 相等；workflowDraft/nodeDrafts/permissions 是跨行全序扫描
// 语义（extractWorkflowDraft / keepLatestPerEntity / buildPermissionViews），
// 输入只有少量视图、成本低，保持全量派生，但输出经结构等价稳定化
// （studioChatViewStability）：内容没变就复用旧引用，否则这些全行 prop
// 每次派生都换新对象，MessageItem 的 memo 照样穿透。
import { useMemo, useState } from 'react'
import {
  buildPermissionViews,
  extractNodeCodeDrafts,
  extractWorkflowDraft,
  type ChatMessage,
  type ToolCallView,
  type WorkflowDraftView,
} from './studioChatMessages'
import { createToolCallDeriver } from './studioChatToolCallDeriver'
import {
  createRefStabilizer,
  sameDraftViews,
  samePermissionViews,
} from './studioChatViewStability'

/** workflow 草稿卡的锚定工具调用：最后一个携带该 yaml 的 validate/compare
 * 调用——草稿卡挂在它后面（原 StudioChatMessageList 内联逻辑，#1120 codex
 * P1 轮为保列表体积预算移入本模块）。 */
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

export function useStudioChatViews(messages: ChatMessage[]) {
  // useState 惰性初始化只用于持有跨渲染的稳定实例；deriver/stabilizer 的
  // 内部缓存均为幂等记忆化。
  const [deriveToolCalls] = useState(() => createToolCallDeriver())
  const [stabilizeDrafts] = useState(() => createRefStabilizer(sameDraftViews))
  const [stabilizePermissions] = useState(() =>
    createRefStabilizer(samePermissionViews)
  )
  const toolCalls = deriveToolCalls(messages)
  const drafts = useMemo(
    () =>
      stabilizeDrafts({
        workflowDraft: extractWorkflowDraft(toolCalls),
        nodeDrafts: extractNodeCodeDrafts(toolCalls),
      }),
    [toolCalls, stabilizeDrafts]
  )
  const permissions = useMemo(
    () => stabilizePermissions(buildPermissionViews(messages)),
    [messages, stabilizePermissions]
  )
  return {
    toolCalls,
    workflowDraft: drafts.workflowDraft,
    nodeDrafts: drafts.nodeDrafts,
    permissions,
  }
}
