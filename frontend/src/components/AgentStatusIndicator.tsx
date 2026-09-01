import { useEffect, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { IconButton, Switch } from '@mui/material'
import { MaterialIcon } from './MaterialIcon'
import { AgentConnectionDot } from './AgentConnectionDot'
import { useAgentsStore } from '../stores/agentsStore'
import { useUiStore } from '../stores/uiStore'
import { AgentWorkerStatusList } from './AgentWorkerStatusList'
import styles from './AgentStatusIndicator.module.css'

export interface AgentStatusIndicatorProps {
  workspaceId: string
}

export function AgentStatusIndicator({
  workspaceId,
}: AgentStatusIndicatorProps) {
  const allAgents = useAgentsStore((state) => state.agents)
  const workerPaused = useAgentsStore((state) =>
    state.getWorkerPaused(workspaceId)
  )
  const fetchWorkerStatus = useAgentsStore((state) => state.fetchWorkerStatus)
  const setWorkerPaused = useAgentsStore((state) => state.setWorkerPaused)
  const showToast = useUiStore((state) => state.showToast)

  const agents = useMemo(
    () => allAgents.filter((agent) => agent.workspace_id === workspaceId),
    [allAgents, workspaceId]
  )
  const busy = agents.some((agent) => agent.busy)
  const monitoringPath = `/workspaces/${workspaceId}/monitoring`

  useEffect(() => {
    // Intentionally silent: a paused-status refresh failure degrades to the
    // last known state and the popover still renders. Unlike togglePause
    // (a user action that needs feedback), this background read would only
    // produce noise with a toast.
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
      <IconButton
        size="small"
        aria-label="Agent 状态"
        sx={{ borderRadius: 2, fontSize: 13, gap: '4px' }}
      >
        <span className={styles.iconWrap}>
          <MaterialIcon name="smart_toy" />
          <span
            aria-hidden="true"
            className={`${styles.indicator} ${busy ? styles.active : ''}`}
          />
          <AgentConnectionDot />
        </span>
        Agent
      </IconButton>
      <div className={styles.popover} role="status">
        <div className={styles.controlRow}>
          <span className={styles.controlLabel}>自动调度</span>
          <Switch checked={!workerPaused} onChange={togglePause} />
        </div>
        <div className={styles.divider} />
        <AgentWorkerStatusList workspaceId={workspaceId} />
        <div className={styles.divider} />
        <Link to={monitoringPath} className={styles.monitorLink}>
          查看监控
        </Link>
      </div>
    </div>
  )
}
