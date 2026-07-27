import { useEffect, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { IconButton, Switch } from '@mui/material'
import { MaterialIcon } from './MaterialIcon'
import { AgentConnectionDot } from './AgentConnectionDot'
import { useUiStore } from '../stores/uiStore'
import { AgentWorkerStatusList } from './AgentWorkerStatusList'
import styles from './AgentStatusIndicator.module.css'

export interface AgentStatusIndicatorProps {
  workspaceId: string
}

export function AgentStatusIndicator({
  workspaceId,
}: AgentStatusIndicatorProps) {
  const allAgents = useUiStore((state) => state.agents)
  const workerPaused = useUiStore((state) => state.getWorkerPaused(workspaceId))
  const fetchWorkerStatus = useUiStore((state) => state.fetchWorkerStatus)
  const setWorkerPaused = useUiStore((state) => state.setWorkerPaused)
  const showToast = useUiStore((state) => state.showToast)

  const agents = useMemo(
    () => allAgents.filter((agent) => agent.workspace_id === workspaceId),
    [allAgents, workspaceId]
  )
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
      <IconButton size="small" aria-label="Agent 状态">
        <MaterialIcon name="smart_toy" />
        <span
          aria-hidden="true"
          className={`${styles.indicator} ${busy ? styles.active : ''}`}
        />
        <AgentConnectionDot />
      </IconButton>
      <div className={styles.popover} role="status">
        <div className={styles.controlRow}>
          <span className={styles.controlLabel}>自动调度</span>
          <Switch checked={!workerPaused} onChange={togglePause} />
        </div>
        <div className={styles.divider} />
        <AgentWorkerStatusList workspaceId={workspaceId} />
        <div className={styles.divider} />
        <Link to="/monitoring" className={styles.monitorLink}>
          查看监控
        </Link>
      </div>
    </div>
  )
}
