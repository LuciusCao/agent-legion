import { useEffect, useState } from 'react'
import { useSettingStore } from '../../../stores/settingStore'
import { useStudioChat } from './useStudioChat'
import { useStudioChatQueue } from './useStudioChatQueue'
import { useStudioContextSync } from './useStudioContextSync'
import { useStudioDraftSync } from './useStudioDraftSync'
import { StudioChatSessionBar } from './StudioChatSessionBar'
import { StudioChatMessageList } from './StudioChatMessageList'
import { StudioChatQueueBar } from './StudioChatQueueBar'
import { StudioChatRunBar } from './StudioChatRunBar'
import { StudioChatInput } from './StudioChatInput'
import styles from './StudioChatPanel.module.css'

type Props = {
  onApplyWorkflowDraft: (yaml: string) => void
  onSelectNode?: (nodeKey: string) => void
  selectedNodeKey?: string | null
  definitionYaml?: string | null
}

/** Studio 右半的 Agent 对话面板（一等公民分栏，不再是 tab）。agent 只能产草稿，
 * 发布永远由人确认（权限提示条常驻）。 */
export function StudioChatPanel(props: Props) {
  const workspaceId = useSettingStore((s) => s.workspaceId) ?? undefined
  const chat = useStudioChat(workspaceId)
  const queue = useStudioChatQueue(chat.busy, chat.activeSessionId, chat.send)
  const sessionId = chat.activeSessionId
  useStudioContextSync(workspaceId, sessionId, props.selectedNodeKey ?? null)
  useStudioDraftSync(workspaceId, sessionId, props.definitionYaml ?? null)
  const [chosenAgentId, setChosenAgentId] = useState('')
  // 未手动选择时跟随 agent 列表第一项（picker 只列本机可用 agent）。
  const selectedAgentId = chosenAgentId || (chat.agents[0]?.id ?? '')

  // 会话列表到达后默认打开最近会话。
  useEffect(() => {
    if (chat.activeSessionId === null && chat.sessions.length > 0) {
      void chat.selectSession(chat.sessions[0].id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只在「未选择」时回填一次
  }, [chat.sessions, chat.activeSessionId])

  if (!workspaceId) {
    return <div className={styles.emptyState}>未选择 workspace</div>
  }
  if (chat.agentsError) {
    return (
      <div className={styles.emptyState}>Agent 列表加载失败，请稍后重试</div>
    )
  }
  if (!chat.agentsLoading && chat.agents.length === 0) {
    return (
      <div className={styles.emptyState}>
        未检测到可用的 ACP agent，请联系管理员配置
      </div>
    )
  }

  // busy（运行中）不再禁用输入：发送会进入前端队列（见 useStudioChatQueue）。
  const inputDisabled = !chat.session || chat.closed
  const disabledReason = !chat.session
    ? '先选择会话或新建对话'
    : chat.closed
      ? '会话已关闭，请新建对话'
      : null

  return (
    <div className={styles.chatPanel}>
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
      {chat.actionError && (
        <div className={styles.statusWarning} role="alert">
          {chat.actionError}
        </div>
      )}
      {chat.activeSessionId === null ? (
        <div className={styles.emptyState}>选择 Agent，点「＋ 新对话」开始</div>
      ) : (
        <StudioChatMessageList
          chat={chat}
          workspaceId={workspaceId}
          onApplyWorkflowDraft={props.onApplyWorkflowDraft}
          onSelectNode={props.onSelectNode}
        />
      )}
      <StudioChatRunBar
        status={chat.session?.status ?? null}
        busy={chat.busy}
        lastRunMs={chat.lastRunMs}
        onCancel={() => void chat.cancel()}
      />
      <StudioChatQueueBar queue={queue} />
      <StudioChatInput
        busy={chat.busy}
        disabled={inputDisabled}
        disabledReason={disabledReason}
        onSend={queue.submit}
      />
    </div>
  )
}
