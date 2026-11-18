import { useId, useMemo, useState } from 'react'
import type { StudioChat } from './useStudioChat'
import type { useStudioChatSessionManage } from './useStudioChatSessionManage'
import { AgentChatPanel } from './AgentChatPanel'
import { StudioChatSessionBar } from './StudioChatSessionBar'
import { StudioChatBackgroundTasks } from './StudioChatBackgroundTasks'
import {
  StudioChatViewTabs,
  type StudioChatView as StudioChatViewKey,
} from './StudioChatViewTabs'
import { deriveStudioBackgroundTaskViews } from './studioBackgroundTaskModel'
import shellStyles from './AgentChatPanel.module.css'
import styles from './StudioChatView.module.css'

export function StudioChatView({
  chat,
  manage,
  workspaceId,
  selectedAgentId,
  onSelectAgent,
  onApplyWorkflowDraft,
  onSelectNode,
}: {
  chat: StudioChat
  manage: ReturnType<typeof useStudioChatSessionManage>
  workspaceId: string
  selectedAgentId: string
  onSelectAgent: (agentId: string) => void
  onApplyWorkflowDraft: (yaml: string) => void
  onSelectNode?: (nodeKey: string) => void
}) {
  const [view, setView] = useState<StudioChatViewKey>('chat')
  const baseId = useId()
  const background = useMemo(
    () => deriveStudioBackgroundTaskViews(chat.messages),
    [chat.messages]
  )
  const timelineChat = useMemo(
    () => ({ ...chat, messages: background.timelineMessages }),
    [chat, background.timelineMessages]
  )

  return (
    <div className={shellStyles.chatPanel}>
      <StudioChatSessionBar
        agents={chat.agents}
        sessions={chat.sessions}
        selectedAgentId={selectedAgentId}
        activeSessionId={chat.activeSessionId}
        onSelectAgent={onSelectAgent}
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
        retentionDays={manage.retentionDays}
      />
      <StudioChatViewTabs
        value={view}
        taskCount={background.tasks.length}
        baseId={baseId}
        onChange={setView}
      />
      <div
        id={`${baseId}-chat-panel`}
        className={styles.conversation}
        role="tabpanel"
        aria-labelledby={`${baseId}-chat-tab`}
        hidden={view !== 'chat'}
      >
        <AgentChatPanel
          chat={timelineChat}
          workspaceId={workspaceId}
          className={styles.conversationPanel}
          showAgentConfig
          emptyState="选择 Agent，点「＋ 新对话」开始"
          noSessionReason="先选择会话或新建对话"
          closedReason="会话已关闭或中断，点「继续对话」恢复"
          onApplyWorkflowDraft={onApplyWorkflowDraft}
          onSelectNode={onSelectNode}
        />
      </div>
      <StudioChatBackgroundTasks
        id={`${baseId}-tasks-panel`}
        labelledBy={`${baseId}-tasks-tab`}
        tasks={background.tasks}
        hidden={view !== 'tasks'}
        sessionId={chat.activeSessionId}
        hasMoreHistory={chat.hasMoreHistory && chat.messages.length > 0}
        onLoadOlderHistory={chat.loadOlderHistory}
      />
    </div>
  )
}
