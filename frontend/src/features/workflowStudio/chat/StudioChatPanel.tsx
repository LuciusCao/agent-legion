import { useState } from 'react'
import { useSettingStore } from '../../../stores/settingStore'
import { useStudioChat } from './useStudioChat'
import { useStudioContextSync } from './useStudioContextSync'
import { useStudioDraftSync } from './useStudioDraftSync'
import { AgentChatPanel } from './AgentChatPanel'
import { StudioChatSessionBar } from './StudioChatSessionBar'
import { useStudioChatSessionManage } from './useStudioChatSessionManage'
import shellStyles from './AgentChatPanel.module.css'

type Props = {
  onApplyWorkflowDraft: (yaml: string) => void
  onSelectNode?: (nodeKey: string) => void
  selectedNodeKey?: string | null
  definitionYaml?: string | null
}

/** Studio 右半的 Agent 对话面板（一等公民分栏，不再是 tab）。agent 只能产草稿，
 * 发布永远由人确认（权限提示条常驻）。外壳复用 AgentChatPanel 骨架（#695）。 */
export function StudioChatPanel(props: Props) {
  const workspaceId = useSettingStore((s) => s.workspaceId) ?? undefined
  const chat = useStudioChat(workspaceId)
  const manage = useStudioChatSessionManage(workspaceId, chat.selectSession)
  const sessionId = chat.activeSessionId
  useStudioContextSync(workspaceId, sessionId, props.selectedNodeKey ?? null)
  useStudioDraftSync(workspaceId, sessionId, props.definitionYaml ?? null)
  const [chosenAgentId, setChosenAgentId] = useState('')
  // 渲染期校验存在性（#797 复审批次 P3，与 CustomizePreviewDock 同款）：
  // 跨 workspace 路由复用组件时残留的 agent id 若不在当前列表即回落默认
  // （key={workspaceId} 重挂之外的双保险），否则「＋ 新对话」会拿旧
  // workspace 的 agent id 请求新 workspace。
  const selectedAgentId =
    chosenAgentId !== '' &&
    chat.agents.some((agent) => agent.id === chosenAgentId)
      ? chosenAgentId
      : (chat.agents[0]?.id ?? '')

  if (!workspaceId) {
    return <div className={shellStyles.emptyState}>未选择 workspace</div>
  }
  if (chat.agentsError) {
    return (
      <div className={shellStyles.emptyState}>
        Agent 列表加载失败，请稍后重试
      </div>
    )
  }
  if (!chat.agentsLoading && chat.agents.length === 0) {
    return (
      <div className={shellStyles.emptyState}>
        未检测到可用的 ACP agent，请联系管理员配置
      </div>
    )
  }

  return (
    <AgentChatPanel
      chat={chat}
      workspaceId={workspaceId}
      header={
        <>
          <StudioChatSessionBar
            agents={chat.agents}
            sessions={chat.sessions}
            selectedAgentId={selectedAgentId}
            activeSessionId={chat.activeSessionId}
            onSelectAgent={setChosenAgentId}
            onSelectSession={(sessionId) => void chat.selectSession(sessionId)}
            onNewChat={() =>
              selectedAgentId && void chat.startSession(selectedAgentId)
            }
            newChatDisabled={!selectedAgentId || chat.starting}
            onRenameSession={manage.rename}
            onDeleteSession={manage.remove}
            archivedSessions={manage.archivedSessions}
            onArchiveSession={manage.archive}
            onUnarchiveSession={manage.unarchive}
          />
        </>
      }
      showAgentConfig
      emptyState="选择 Agent，点「＋ 新对话」开始"
      noSessionReason="先选择会话或新建对话"
      closedReason="会话已关闭或中断，点「继续对话」恢复"
      onApplyWorkflowDraft={props.onApplyWorkflowDraft}
      onSelectNode={props.onSelectNode}
    />
  )
}
