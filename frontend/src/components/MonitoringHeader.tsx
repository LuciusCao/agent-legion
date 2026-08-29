import {
  FormControl,
  MenuItem,
  Select,
  ToggleButton,
  ToggleButtonGroup,
} from '@mui/material'
import type { OpsGranularity } from '../api/metrics'
import type { listAgentWorkers } from '../api/agentWorkers'
import styles from './MonitoringPanel.module.css'

type WorkerSummary = Awaited<ReturnType<typeof listAgentWorkers>>[number]

interface MonitoringHeaderProps {
  workspaceId?: string
  granularity: OpsGranularity
  onGranularityChange: (value: OpsGranularity) => void
  workerId: string
  onWorkerChange: (value: string) => void
  workers: WorkerSummary[]
}

/** 监控页头：标题/副标题 + Worker 过滤器（仅全局视图）+ 时间粒度切换。 */
export function MonitoringHeader({
  workspaceId,
  granularity,
  onGranularityChange,
  workerId,
  onWorkerChange,
  workers,
}: MonitoringHeaderProps) {
  return (
    <header className={styles.header}>
      <div className={styles.titleGroup}>
        <h2>运维监控</h2>
        <p>
          {workspaceId
            ? `workspace「${workspaceId}」的队列、执行并发与 token 趋势，每 30 秒自动刷新。`
            : '在线 Worker、执行并发与 token 吞吐趋势，每 30 秒自动刷新。'}
        </p>
      </div>
      <div className={styles.controls}>
        {!workspaceId && (
          <FormControl size="small">
            <Select
              value={workerId}
              onChange={(e) => onWorkerChange(e.target.value)}
              displayEmpty
              inputProps={{ 'aria-label': '选择 Worker' }}
            >
              <MenuItem value="">全部 Worker</MenuItem>
              {workers.map((w) => (
                <MenuItem key={w.worker_id} value={w.worker_id}>
                  {w.name || w.worker_id}
                  {w.online ? '（在线）' : '（离线）'}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        )}
        <ToggleButtonGroup
          size="small"
          exclusive
          value={granularity}
          onChange={(_e, value: OpsGranularity | null) => {
            if (value) onGranularityChange(value)
          }}
          aria-label="时间粒度"
        >
          <ToggleButton value="6h">近 6 小时</ToggleButton>
          <ToggleButton value="24h">近 24 小时</ToggleButton>
          <ToggleButton value="30d">近 30 天</ToggleButton>
        </ToggleButtonGroup>
      </div>
    </header>
  )
}
