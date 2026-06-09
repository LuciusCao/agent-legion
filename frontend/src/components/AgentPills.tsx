import styles from './AgentPills.module.css'

export interface AgentPillsProps {
  agents: Array<{ id: string; status: 'idle' | 'busy'; currentJobId?: string }>
  maxConcurrency: number
}

export function AgentPills({ agents, maxConcurrency }: AgentPillsProps) {
  const idleCount = agents.filter((a) => a.status === 'idle').length

  return (
    <div className={styles.row}>
      <div className={styles.pills}>
        {agents.map((agent) => (
          <span
            key={agent.id}
            data-agent={agent.id}
            className={`${styles.pill} ${
              agent.status === 'idle' ? styles.idle : styles.busy
            }`}
          >
            <span className={styles.dot} />
            <span>{agent.id}</span>
            {agent.status === 'busy' && agent.currentJobId && (
              <span className={styles.job}>· {agent.currentJobId}</span>
            )}
          </span>
        ))}
      </div>
      <span className={styles.summary}>
        {idleCount}/{agents.length} 可用 · 并发上限 {maxConcurrency}
      </span>
    </div>
  )
}
