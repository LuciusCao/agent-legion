import { useEffect, useMemo } from 'react'
import { useExecutorsStore } from '../stores/executorsStore'
import styles from './AgentWorkerStatusList.module.css'

export interface AgentWorkerStatusListProps {
  workspaceId: string
}

export function AgentWorkerStatusList({
  workspaceId,
}: AgentWorkerStatusListProps) {
  const workers = useExecutorsStore((state) => state.workers)
  const refreshWorkers = useExecutorsStore((state) => state.refreshWorkers)

  // Backend online threshold is 30s; a 15s poll keeps the status fresh.
  useEffect(() => {
    void refreshWorkers()
    const timer = setInterval(() => void refreshWorkers(), 15000)
    return () => clearInterval(timer)
  }, [refreshWorkers])

  const visibleWorkers = useMemo(
    () =>
      workers.filter(
        (worker) =>
          !worker.revoked &&
          (worker.allowed_workspaces.length === 0 ||
            worker.allowed_workspaces.includes(workspaceId))
      ),
    [workers, workspaceId]
  )

  return (
    <>
      <div className={styles.sectionLabel}>已注册 Worker</div>
      {visibleWorkers.length === 0 ? (
        <div className={styles.empty}>暂无已注册 Worker</div>
      ) : (
        visibleWorkers.map((worker) => (
          <div className={styles.row} key={worker.worker_id}>
            <span>{worker.name || worker.worker_id}</span>
            <span
              className={`${styles.chip} ${worker.online ? styles.chipOnline : styles.chipOffline}`}
              title={`最近心跳 ${worker.last_seen_at}`}
            >
              {worker.online ? '在线' : '离线'}
            </span>
          </div>
        ))
      )}
    </>
  )
}
