/**
 * 「定制预览」Dock 面板（issue #328；#795 PR① 从右侧 MUI Dialog 迁移进
 * AgentPanelDock；#796 验收返工）：AgentPanelDock + AgentChatPanel（定制
 * 预览会话）的薄组合——Dock 是纯对话容器，面板内不长私有 UI（无引导文案、
 * 无治理 footer）。agent 经 MCP 预览面板工具写草稿；治理动作（预览此草稿/
 * 发布/恢复默认）与草稿状态行在 job detail 左栏「内容预览」区头部
 * （PreviewPanelHeader），草稿渲染目标是左栏既有 PreviewPanelHost 通道
 * （#347 P1 逐次授权语义全部在 PreviewPanelSection）。
 */
import { useState } from 'react'
import { AgentPanelDock } from '../agentPanelDock/AgentPanelDock'
import { useStudioChat } from '../workflowStudio/chat/useStudioChat'
import { AgentChatPanel } from '../workflowStudio/chat/AgentChatPanel'
import { StudioChatSessionBar } from '../workflowStudio/chat/StudioChatSessionBar'
import styles from './CustomizePreviewDock.module.css'

export interface CustomizePreviewDockProps {
  workspaceId: string
  onClose: () => void
}

export function CustomizePreviewDock({
  workspaceId,
  onClose,
}: CustomizePreviewDockProps) {
  const chat = useStudioChat(workspaceId)
  const [chosenAgentId, setChosenAgentId] = useState('')
  const selectedAgentId = chosenAgentId || (chat.agents[0]?.id ?? '')

  return (
    <AgentPanelDock
      surfaceKey="customize-preview"
      title="定制预览面板"
      defaultSize={{ width: 480, height: 620 }}
      minWidth={340}
      onClose={onClose}
    >
      <div className={styles.body}>
        {chat.agentsError ? (
          <div className={styles.error}>Agent 列表加载失败，请稍后重试</div>
        ) : !chat.agentsLoading && chat.agents.length === 0 ? (
          <div className={styles.hint}>
            未检测到可用的 ACP agent，请联系管理员配置
          </div>
        ) : (
          <AgentChatPanel
            chat={chat}
            workspaceId={workspaceId}
            className={styles.chatArea}
            header={
              <StudioChatSessionBar
                agents={chat.agents}
                sessions={chat.sessions}
                selectedAgentId={selectedAgentId}
                activeSessionId={chat.activeSessionId}
                onSelectAgent={setChosenAgentId}
                onSelectSession={(sessionId) =>
                  void chat.selectSession(sessionId)
                }
                onNewChat={() =>
                  selectedAgentId && void chat.startSession(selectedAgentId)
                }
                newChatDisabled={!selectedAgentId || chat.starting}
              />
            }
            emptyState="选择 Agent，点「＋ 新对话」开始"
            noSessionReason="先选择会话或新建对话"
            closedReason="会话已关闭或中断，点「继续对话」恢复"
            onApplyWorkflowDraft={() => undefined}
          />
        )}
      </div>
    </AgentPanelDock>
  )
}
