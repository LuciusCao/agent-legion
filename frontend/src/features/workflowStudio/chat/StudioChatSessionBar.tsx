import { createPortal } from 'react-dom'
import type {
  StudioChatAgentOption,
  StudioChatSessionRecord,
} from './studioChatApi'
import { useDockTitleSlot } from '../../agentPanelDock/dockTitleSlot'
import { StudioChatSessionMenu } from './StudioChatSessionMenu'
import styles from './StudioChatPanel.module.css'

type Props = {
  agents: StudioChatAgentOption[]
  sessions: StudioChatSessionRecord[]
  selectedAgentId: string
  activeSessionId: string | null
  onSelectAgent: (agentId: string) => void
  onSelectSession: (sessionId: string) => void
  onNewChat: () => void
  newChatDisabled: boolean
  /** 会话管理（#872）：改名 / 删除，失败 reject 由会话菜单行内展示。 */
  onRenameSession?: (sessionId: string, title: string) => Promise<void>
  onDeleteSession?: (sessionId: string) => Promise<void>
}

/** 会话管理条：Agent 选择 + 会话菜单 + 新对话。#825：在 AgentPanelDock 内
 * 时 portal 进 Dock 标题行（与「Agent 助手」标题同一行，不再单独占一行）；
 * 不在 Dock 内时回落为面板顶部的独立行。 */
export function StudioChatSessionBar(props: Props) {
  const titleSlot = useDockTitleSlot()
  const bar = (
    <div className={titleSlot ? styles.sessionBarInTitle : styles.sessionBar}>
      <select
        className={styles.picker}
        aria-label="选择 Agent"
        title="Agent"
        value={props.selectedAgentId}
        onChange={(event) => props.onSelectAgent(event.target.value)}
      >
        {props.agents.map((agent) => (
          <option key={agent.id} value={agent.id}>
            {agent.label}
          </option>
        ))}
      </select>
      <StudioChatSessionMenu
        sessions={props.sessions}
        activeSessionId={props.activeSessionId}
        onSelectSession={props.onSelectSession}
        onRename={props.onRenameSession}
        onDelete={props.onDeleteSession}
      />
      <button
        type="button"
        className={styles.newChat}
        onClick={props.onNewChat}
        disabled={props.newChatDisabled}
      >
        ＋ 新对话
      </button>
    </div>
  )
  return titleSlot ? createPortal(bar, titleSlot) : bar
}
