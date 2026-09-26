import { useState } from 'react'
import { useSettingStore } from '../../../stores/settingStore'
import { useStudioChat } from './useStudioChat'
import { useStudioContextSync } from './useStudioContextSync'
import { useStudioDraftSync } from './useStudioDraftSync'
import { AgentChatPanel } from './AgentChatPanel'
import { StudioChatSessionBar } from './StudioChatSessionBar'
import styles from './StudioChatPanel.module.css'
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
  const sessionId = chat.activeSessionId
  useStudioContextSync(workspaceId, sessionId, props.selectedNodeKey ?? null)
  useStudioDraftSync(workspaceId, sessionId, props.definitionYaml ?? null)
  const [chosenAgentId, setChosenAgentId] = useState('')
  // 未手动选择时跟随 agent 列表第一项（picker 只列本机可用 agent）。
  const selectedAgentId = chosenAgentId || (chat.agents[0]?.id ?? '')

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
          />
          <div className={styles.scopeNote}>
            Agent 来自管理员配置并按本机安装过滤；agent 只能产出草稿与校验，
            <b>发布永远由你确认</b>。
          </div>
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
