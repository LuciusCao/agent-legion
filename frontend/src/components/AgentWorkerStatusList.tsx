import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { listAgentWorkers } from '../api/workerTokens'
import { queryKeys } from '../lib/queryKeys'
import { useAgentsStore } from '../stores/agentsStore'
import { buildWorkerRows } from './agentWorkerRows'
import styles from './AgentWorkerStatusList.module.css'

export interface AgentWorkerStatusListProps {
  workspaceId: string
}

export function AgentWorkerStatusList({
  workspaceId,
}: AgentWorkerStatusListProps) {
  // Backend online threshold is 30s; a 15s poll keeps the status fresh.
  const { data: workers = [] } = useQuery({
    queryKey: queryKeys.agentWorkers(),
    queryFn: listAgentWorkers,
    refetchInterval: 15_000,
  })
  const allAgents = useAgentsStore((state) => state.agents)

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
