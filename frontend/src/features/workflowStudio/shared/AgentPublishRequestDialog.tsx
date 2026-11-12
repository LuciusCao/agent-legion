import { useEffect } from 'react'
import { useStudioState, useStudioView } from './studioStateContext'
import { useAgentPublishRequest } from './useAgentPublishRequest'
import { useAgentPublishNoticeStore } from './agentPublishNoticeStore'
import { useSettingStore } from '../../../stores/settingStore'
import { useUiStore } from '../../../stores/uiStore'
import { confirmAgentPublishRequest } from './agentPublishRequestActions'
import { AgentPublishRequestDialogView } from './AgentPublishRequestDialogView'

export { reviewDialogProps } from './AgentPublishRequestDialogView'

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
  const showToast = useUiStore((s) => s.showToast)
  const workspaceId = useSettingStore((s) => s.workspaceId) ?? undefined
  // 相同 queryKey 的 useQuery 与 StudioChatAside 的轮询自动合并。
  const agentRequest = useAgentPublishRequest(workspaceId)
  // M65：把挂起态镜像进 agentPublishNoticeStore——Workflow 草稿卡的
  // 「发布请求挂起」引导条读 store（卡片可在无 QueryClient 的测试环境
  // 直渲染，不能直接挂 useAgentPublishRequest 的轮询 hook）。
  useEffect(() => {
    useAgentPublishNoticeStore
      .getState()
      .setPendingRequestId(agentRequest.pendingRequest?.id ?? null)
  }, [agentRequest.pendingRequest])
  const confirmAgentRequest = async () => {
    const shouldOpenChanges = await confirmAgentPublishRequest({
      workspaceId,
      studio,
      agentRequest,
      showToast,
    })
    if (shouldOpenChanges) view.setChangesPanelOpen(true)
  }
  return (
    <AgentPublishRequestDialogView
      studio={studio}
      agentRequest={agentRequest}
      onConfirm={confirmAgentRequest}
    />
  )
}
