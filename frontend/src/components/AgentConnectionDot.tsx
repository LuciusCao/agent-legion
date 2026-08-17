import { useConnectionStatusStore } from '../stores/connectionStatusStore'
import styles from './AgentConnectionDot.module.css'

const TITLES = {
  closed: 'Agent 连接已断开',
  connecting: 'Agent 连接中',
} as const

export function AgentConnectionDot() {
  const status = useConnectionStatusStore(
    (state) => state.connectionStatus['agents']
  )
  if (status !== 'closed' && status !== 'connecting') return null
  return (
    <span
      data-testid="agents-connection-status"
      title={TITLES[status]}
      className={`${styles.connDot} ${status === 'closed' ? styles.connClosed : ''}`}
    />
  )
}
