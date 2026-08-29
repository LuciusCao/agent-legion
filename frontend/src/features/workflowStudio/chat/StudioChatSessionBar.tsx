import type {
  StudioChatAgentOption,
  StudioChatSessionRecord,
} from './studioChatApi'
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
}

function sessionLabel(session: StudioChatSessionRecord): string {
  if (session.title) return session.title
  return `对话 ${session.created_at.slice(0, 16).replace('T', ' ')}`
}

export function StudioChatSessionBar(props: Props) {
  return (
    <div className={styles.sessionBar}>
      <label className={styles.pickerLabel}>
        Agent
        <select
          className={styles.picker}
          aria-label="选择 Agent"
          value={props.selectedAgentId}
          onChange={(event) => props.onSelectAgent(event.target.value)}
        >
          {props.agents.map((agent) => (
            <option key={agent.id} value={agent.id}>
              {agent.label}
            </option>
          ))}
        </select>
      </label>
      <label className={styles.pickerLabel}>
        会话
        <select
          className={styles.picker}
          aria-label="选择会话"
          value={props.activeSessionId ?? ''}
          onChange={(event) => props.onSelectSession(event.target.value)}
        >
          {props.activeSessionId === null && <option value="">未选择</option>}
          {props.sessions.map((session) => (
            <option key={session.id} value={session.id}>
              {sessionLabel(session)}
              {session.status === 'closed' ? '（已关闭）' : ''}
            </option>
          ))}
        </select>
      </label>
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
}
