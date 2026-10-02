import { useEffect } from 'react'
import { Button } from '@mui/material'
import { MaterialIcon } from './MaterialIcon'
import { AgentConnectionDot } from './AgentConnectionDot'
import { useAgentsStore } from '../stores/agentsStore'
import { useWorkerScheduling } from '../hooks/useWorkerScheduling'
import { AgentWorkerStatusList } from './AgentWorkerStatusList'
import styles from './WorkspaceRunControl.module.css'

export interface WorkspaceRunControlProps {
  workspaceId: string
}

export function WorkspaceRunControl({ workspaceId }: WorkspaceRunControlProps) {
  const workerPaused = useAgentsStore((state) =>
    state.getWorkerPaused(workspaceId)
  )
  const fetchWorkerStatus = useAgentsStore((state) => state.fetchWorkerStatus)
  const setWorkerPaused = useWorkerScheduling(workspaceId)

  useEffect(() => {
    // Intentionally silent: a paused-status refresh failure degrades to the
    // last known state and the popover still renders. Unlike togglePause
    // (a user action that needs feedback), this background read would only
    // produce noise with a toast.
    fetchWorkerStatus(workspaceId).catch(() => {})
  }, [fetchWorkerStatus, workspaceId])

  return (
    <div className={styles.root}>
      <Button
        size="small"
        aria-label={workerPaused ? '恢复运行' : '暂停运行'}
        onClick={() => void setWorkerPaused(!workerPaused)}
        startIcon={
          <span className={styles.iconWrap}>
            <MaterialIcon
              name={workerPaused ? 'play_arrow' : 'pause'}
              sx={{ fontSize: 20 }}
            />
            <AgentConnectionDot />
          </span>
        }
        sx={{
          color: 'inherit',
          minWidth: 0,
          px: 1,
          whiteSpace: 'nowrap',
          fontSize: 13,
          '& .MuiButton-startIcon': { marginRight: '4px' },
        }}
      >
        {workerPaused ? '已暂停' : '运行中'}
      </Button>
      <div className={styles.popover} role="status">
        <AgentWorkerStatusList workspaceId={workspaceId} />
      </div>
    </div>
  )
}
