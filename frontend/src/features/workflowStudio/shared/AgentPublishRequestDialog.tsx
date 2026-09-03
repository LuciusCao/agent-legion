import { WorkflowPublishReviewDialog } from '../validation/WorkflowPublishReviewDialog'
import type { ChangeSummaryViewModel } from '../validation/workflowStudioChanges'
import type { WorkflowRevisionSummary } from '../../../types'
import { useStudioState, useStudioView } from './studioStateContext'
import { useAgentPublishRequest } from './useAgentPublishRequest'
import { useSettingStore } from '../../../stores/settingStore'

/** #416：agent 发起的 workflow 发布请求确认对话框。复用手动发布的
 * WorkflowPublishReviewDialog（同一份 compare summary 数据流）：
 * - pending 请求存在且手动对话框未开时弹出（手动流程优先，不叠加）；
 * - 确认 → 后端确认端点（与手动发布同门禁，非前端直接 publishDraft）；
 * - 取消 → 后端取消端点，agent 可继续修改草稿再发起。
 * agent 的 scoped token 在后端被 reject_studio_agent_scope 挡在门外，
 * 确认/取消只可能来自用户会话。 */
export function AgentPublishRequestDialog() {
  const studio = useStudioState()
  const view = useStudioView()
  const workspaceId = useSettingStore((s) => s.workspaceId) ?? undefined
  // 相同 queryKey 的 useQuery 与 StudioChatAside 的轮询自动合并。
  const agentRequest = useAgentPublishRequest(workspaceId)
  if (agentRequest.pendingRequest === null) return null
  const open = !studio.reviewDialogOpen
  return (
    <WorkflowPublishReviewDialog
      open={open}
      {...reviewDialogProps(studio)}
      confirming={agentRequest.confirming}
      canceling={agentRequest.canceling}
      onConfirm={async () => {
        await agentRequest.confirm()
        view.setChangesPanelOpen(true)
      }}
      onCancel={() => {
        void agentRequest.cancel()
      }}
    />
  )
}

/** 手动与 agent 两个确认对话框共享的 props（workflow/revision/compare
 * 数据流）：抽出来供 WorkflowStudioLayoutDialogs 复用，两个对话框对同
 * 一份草稿展示同一份比对结果。 */
export function reviewDialogProps(studio: {
  workflow?: { key: string } | null
  revision?: WorkflowRevisionSummary | null
  createsRevision?: boolean
  compareSummary?: ChangeSummaryViewModel | null
}) {
  return {
    workflowKey: studio.workflow?.key ?? null,
    activeRevision: studio.revision ?? null,
    nextVersion: (studio.revision?.version ?? 0) + 1,
    createsRevision: studio.createsRevision,
    definitionHash: studio.revision?.definition_hash ?? null,
    summary: studio.compareSummary ?? null,
  }
}
