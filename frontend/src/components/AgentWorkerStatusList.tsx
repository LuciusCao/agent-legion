import { useEffect, useMemo } from 'react'
import { useExecutorsStore } from '../stores/executorsStore'
import { useUiStore } from '../stores/uiStore'
import { buildWorkerRows } from './agentWorkerRows'
import styles from './AgentWorkerStatusList.module.css'

export interface AgentWorkerStatusListProps {
  workspaceId: string
}

export function AgentWorkerStatusList({
  workspaceId,
}: AgentWorkerStatusListProps) {
  const workers = useExecutorsStore((state) => state.workers)
  const refreshWorkers = useExecutorsStore((state) => state.refreshWorkers)
  const allAgents = useUiStore((state) => state.agents)

  // Backend online threshold is 30s; a 15s poll keeps the status fresh.
  useEffect(() => {
    void refreshWorkers()
    const timer = setInterval(() => void refreshWorkers(), 15000)
    return () => clearInterval(timer)
  }, [refreshWorkers])

  const rows = useMemo(
    () => buildWorkerRows(workers, allAgents, workspaceId),
    [workers, allAgents, workspaceId]
  )

  return (
    <>
      <div className={styles.sectionLabel}>已注册 Worker</div>
      {rows.length === 0 ? (
        <div className={styles.empty}>暂无可用 Worker</div>
      ) : (
        rows.map((row) => (
          <div className={styles.row} key={row.key}>
            {row.online === null ? (
              <span className={`${styles.chip} ${styles.chipHidden}`}>
                在线
              </span>
            ) : (
              <span
                className={`${styles.chip} ${row.online ? styles.chipOnline : styles.chipOffline}`}
                title={row.heartbeatTitle}
              >
                {row.online ? '在线' : '离线'}
              </span>
            )}
            <span className={styles.name}>{row.name}</span>
            <span className={styles.workload}>{row.workload}</span>
          </div>
        ))
      )}
    </>
  )
}
