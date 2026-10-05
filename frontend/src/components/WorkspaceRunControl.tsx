import { Button } from '@mui/material'
import { MaterialIcon } from './MaterialIcon'
import { AgentConnectionDot } from './AgentConnectionDot'
import { useWorkerScheduling } from '../hooks/useWorkerScheduling'
import { useWorkerPausedStatus } from '../hooks/useWorkerPausedStatus'
import { AgentWorkerStatusList } from './AgentWorkerStatusList'
import { runControlView } from './workspaceRunControlView'
import styles from './WorkspaceRunControl.module.css'

export interface WorkspaceRunControlProps {
  workspaceId: string
}

export function WorkspaceRunControl({ workspaceId }: WorkspaceRunControlProps) {
  // #961：暂停位唯一来源是 RQ 缓存；拉取中/失败不得冒充「已暂停」。
  // 失败态（含刷新失败）显示「状态未知」，点击重试拉取而不是盲目切换。
  const status = useWorkerPausedStatus(workspaceId)
  const setWorkerPaused = useWorkerScheduling(workspaceId)
  const view = runControlView(status)
  const onClick = () => {
    if (view.kind === 'unknown') void status.refetch()
    else if (view.kind !== 'loading') void setWorkerPaused(!view.paused)
  }

  return (
    <div className={styles.root}>
      <Button
        size="small"
        aria-label={view.ariaLabel}
        title={
          view.kind === 'unknown' ? '运行状态拉取失败，点击重试' : undefined
        }
        disabled={view.kind === 'loading'}
        onClick={onClick}
        startIcon={
          <span className={styles.iconWrap}>
            <MaterialIcon name={view.icon} sx={{ fontSize: 20 }} />
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
        {view.label}
      </Button>
      <div className={styles.popover} role="status">
        <AgentWorkerStatusList workspaceId={workspaceId} />
      </div>
    </div>
  )
}
