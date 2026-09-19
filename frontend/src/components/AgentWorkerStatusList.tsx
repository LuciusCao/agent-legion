import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { listAgentWorkers } from '../api/agentWorkers'
import { queryKeys } from '../lib/queryKeys'
import { useAgentsStore } from '../stores/agentsStore'
import { buildWorkerRows } from './agentWorkerRows'
import { useWorkerConsoleUrl } from '../hooks/useWorkerConsoleUrl'
import {
  PRESENCE_LABEL,
  presenceTitle,
  type WorkerPresence,
} from '../lib/workerPresence'
import { WorkerConsoleLink } from './WorkerConsoleLink'
import styles from './AgentWorkerStatusList.module.css'

// 「在线·未领取」用警示色：它是「任务一直等待中」的首要嫌疑。
const PRESENCE_CLASS: Record<WorkerPresence, string> = {
  offline: styles.chipOffline,
  online: styles.chipOnline,
  claiming: styles.chipOnline,
  not_claiming: styles.chipIdle,
}

export interface AgentWorkerStatusListProps {
  workspaceId: string
}

export function AgentWorkerStatusList({
  workspaceId,
}: AgentWorkerStatusListProps) {
  // Backend online threshold is 30s; a 15s poll keeps the status fresh.
  const { data: workers = [] } = useQuery({
    queryKey: queryKeys.agentWorkers(),
    queryFn: () => listAgentWorkers(),
    refetchInterval: 15_000,
  })
  const allAgents = useAgentsStore((state) => state.agents)
  const consoleUrl = useWorkerConsoleUrl() ?? ''

  const rows = useMemo(
    () => buildWorkerRows(workers, allAgents, workspaceId),
    [workers, allAgents, workspaceId]
  )

  return (
    <>
      <div className={styles.sectionLabel}>已注册 Worker</div>
      {rows.length === 0 ? (
        <div className={styles.empty}>
          暂无可用 Worker：需在 Worker 控制台添加本 workspace 的 Key
          并「开始领取」。 <WorkerConsoleLink url={consoleUrl} />
        </div>
      ) : (
        rows.map((row) => (
          <div className={styles.row} key={row.key}>
            {row.online === null ? (
              <span className={`${styles.chip} ${styles.chipHidden}`}>
                在线
              </span>
            ) : (
              <span
                className={`${styles.chip} ${PRESENCE_CLASS[row.presence]}`}
                title={presenceTitle(row.presence, row.heartbeatTitle)}
              >
                {PRESENCE_LABEL[row.presence]}
              </span>
            )}
            <span className={styles.name}>{row.name}</span>
            <span className={styles.workload}>{row.workload}</span>
            <WorkerConsoleLink url={row.consoleUrl} label="控制台" />
          </div>
        ))
      )}
    </>
  )
}
