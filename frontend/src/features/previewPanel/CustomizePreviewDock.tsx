/**
 * 「定制预览」Dock 面板（issue #328；#795 PR① 从右侧 MUI Dialog 迁移进
 * AgentPanelDock；#796 验收返工）：AgentPanelDock + AgentChatPanel（定制
 * 预览会话）的薄组合——Dock 是纯对话容器，面板内不长私有 UI（无引导文案、
 * 无治理 footer）。agent 经 MCP 预览面板工具写草稿；治理动作（预览此草稿/
 * 发布/恢复默认）与草稿状态行在 job detail 左栏「内容预览」区头部
 * （PreviewPanelHeader），草稿渲染目标是左栏既有 PreviewPanelHost 通道
 * （#347 P1 逐次授权语义全部在 PreviewPanelSection）。
 * 跨 workspace 隔离（codex P2 复审轮）：useStudioChat 连同聊天子树收进
 * 带 key={workspaceId} 的 CustomizePreviewChat——react-router 复用 Dock
 * 实例跨 workspace 导航时整棵重挂（hook 的 actionError/starting/会话选择
 * 与 composer 未发送文本一律不串）；useStudioChat 内部的迟到响应另有
 * workspaceId 快照守卫（双保险，Studio 侧共享该 hook）。
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

/** Dock 的对话内容（按 workspaceId 重挂的单元，见文件头注释）。 */
function CustomizePreviewChat({ workspaceId }: { workspaceId: string }) {
  const chat = useStudioChat(workspaceId)
  // 渲染期校验存在性：agent 列表异步到达/跨 workspace 变化时，残留的选择
  // id 若不在当前列表里即回落默认（重挂之外的双保险）。
  const [chosenAgentId, setChosenAgentId] = useState('')
  const selectedAgentId =
    chosenAgentId !== '' &&
    chat.agents.some((agent) => agent.id === chosenAgentId)
      ? chosenAgentId
      : (chat.agents[0]?.id ?? '')

  return (
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
          showAgentConfig
          onApplyWorkflowDraft={() => undefined}
        />
      )}
    </div>
  )
}

export function CustomizePreviewDock({
  workspaceId,
  onClose,
}: CustomizePreviewDockProps) {
  return (
    <AgentPanelDock
      surfaceKey="customize-preview"
      title="定制预览面板"
      defaultSize={{ width: 480, height: 620 }}
      minWidth={340}
      // 焦点归还指定头部「定制预览」入口（关闭路径的稳定恢复目标）。
      restoreFocusSelector='[data-testid="customize-preview-trigger"]'
      onClose={onClose}
    >
      <CustomizePreviewChat key={workspaceId} workspaceId={workspaceId} />
    </AgentPanelDock>
  )
}
