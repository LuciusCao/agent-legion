import { useMemo } from 'react'
import { useUiStore } from '../stores/uiStore'
import styles from './AgentStatusIndicator.module.css'

export function AgentStatusIndicator() {
  const allAgents = useUiStore((state) => state.agents)
  const agents = useMemo(
    () => allAgents.filter((agent) => !agent.workspace_id),
    [allAgents]
  )
  const busy = agents.some((agent) => agent.busy)

  return (
    <div className={styles.root}>
      <md-icon-button aria-label="Agent 状态">
        <md-icon>smart_toy</md-icon>
        <span
          aria-hidden="true"
          className={`${styles.indicator} ${busy ? styles.active : ''}`}
        />
      </md-icon-button>
      <div className={styles.popover} role="status">
        {agents.length === 0 ? (
          <div className={styles.empty}>暂无运行中的 Agent</div>
        ) : (
          agents.map((agent) => (
            <div className={styles.row} key={agent.id}>
              <span>{agent.name || agent.id}</span>
              <span className={styles.status}>
                {agent.busy
                  ? `忙碌 (${agent.task_count}/${agent.max_tasks})`
                  : '空闲'}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
