// #1120 PR-2：deriveChatViews 的引用稳定化包装（MessageItem memo 修复）。
// toolCalls 走 createToolCallDeriver 增量归并——未触动消息的 ToolCallView
// 保持 Object.is 相等；workflowDraft/nodeDrafts/permissions 是跨行全序扫描
// 语义（extractWorkflowDraft / keepLatestPerEntity / buildPermissionViews），
// 输入只有少量视图、成本低，保持全量派生，但输出经结构等价稳定化
// （studioChatViewStability）：内容没变就复用旧引用，否则这些全行 prop
// 每次派生都换新对象，MessageItem 的 memo 照样穿透。
// #1123：草稿视图在 tool_call 派生之上再合并合成 draft_saved 事件
// （studioChatDraftEvents）——子代理保存的唯一卡片数据源；同实体按证据
// 时刻取最新，不重复出卡。
import { useMemo, useState } from 'react'
import {
  buildPermissionViews,
  extractNodeCodeDrafts,
  extractWorkflowDraft,
  type ChatMessage,
} from './studioChatMessages'
import {
  mergeNodeCodeDrafts,
  mergeWorkflowDraft,
} from './studioChatDraftEvents'
import { createToolCallDeriver } from './studioChatToolCallDeriver'
import {
  createRefStabilizer,
  sameDraftViews,
  samePermissionViews,
} from './studioChatViewStability'

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
        workflowDraft: mergeWorkflowDraft(
          extractWorkflowDraft(toolCalls),
          toolCalls,
          messages
        ),
        nodeDrafts: mergeNodeCodeDrafts(
          extractNodeCodeDrafts(toolCalls),
          messages
        ),
      }),
    [toolCalls, messages, stabilizeDrafts]
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
