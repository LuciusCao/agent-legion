import { useEffect, useMemo } from 'react'
import { IconButton, Switch } from '@mui/material'
import { MaterialIcon } from './MaterialIcon'
import { useUiStore } from '../stores/uiStore'
import styles from './AgentStatusIndicator.module.css'

export interface AgentStatusIndicatorProps {
  workspaceId?: string
}

export function AgentStatusIndicator({
  workspaceId,
}: AgentStatusIndicatorProps) {
  const allAgents = useUiStore((state) => state.agents)
  const workerPaused = useUiStore((state) => state.getWorkerPaused(workspaceId))
  const fetchWorkerStatus = useUiStore((state) => state.fetchWorkerStatus)
  const setWorkerPaused = useUiStore((state) => state.setWorkerPaused)
  const showToast = useUiStore((state) => state.showToast)

  const agents = useMemo(() => {
    if (!workspaceId || workspaceId === 'video-hive') {
      return allAgents.filter((agent) => !agent.workspace_id)
    }
    return allAgents.filter((agent) => agent.workspace_id === workspaceId)
  }, [allAgents, workspaceId])
  const busy = agents.some((agent) => agent.busy)

  useEffect(() => {
    fetchWorkerStatus(workspaceId).catch(() => {})
  }, [fetchWorkerStatus, workspaceId])

  const togglePause = async () => {
    const next = !workerPaused
    try {
      await setWorkerPaused(next, workspaceId)
      showToast(next ? '已暂停自动调度' : '已恢复自动调度', 'success')
    } catch {
      showToast('更新失败', 'error')
    }
  }

  return (
    <div className={styles.root}>
      <IconButton aria-label="Agent 状态">
        <MaterialIcon name="smart_toy" />
        <span
          aria-hidden="true"
          className={`${styles.indicator} ${busy ? styles.active : ''}`}
        />
      </IconButton>
      <div className={styles.popover} role="status">
        <div className={styles.controlRow}>
          <span className={styles.controlLabel}>自动调度</span>
          <Switch checked={!workerPaused} onChange={togglePause} />
        </div>
        <div className={styles.divider} />
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
