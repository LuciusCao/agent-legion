import { Button } from '@mui/material'
import { WORKSPACE_LABELS } from '../labels'
import type { ExecutorRuntimeStatus } from '../types/workspaceTypes'

type WorkspaceCardProps = {
  name: string
  workflowLabel: string
  jobStats: Record<string, number>
  executorStatus: ExecutorRuntimeStatus[]
  onClick: () => void
}

export default function WorkspaceCard({
  name,
  workflowLabel,
  jobStats,
  executorStatus,
  onClick,
}: WorkspaceCardProps) {
  const total = Object.values(jobStats).reduce((a, b) => a + b, 0)
  const running = jobStats['running'] || 0
  const completed = jobStats['completed'] || 0
  const failed = jobStats['failed'] || 0
  const executorRunning = executorStatus.reduce(
    (sum, e) => sum + (e.running || 0),
    0
  )
  const executorAvailable = executorStatus.reduce(
    (sum, e) => sum + (e.available || 0),
    0
  )

  return (
    <div
      data-testid="workspace-card"
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onClick()
        }
      }}
      style={{
        borderRadius: 16,
        padding: 20,
        background: '#f5f5f5',
        cursor: 'pointer',
        border: '1px solid #e0e0e0',
        transition: 'box-shadow 0.2s',
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.boxShadow = '0 2px 8px rgba(0,0,0,0.1)'
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.boxShadow = 'none'
      }}
    >
      <div>
        <h3 style={{ margin: '0 0 4px', fontSize: 20 }}>{name}</h3>
        <span
          style={{
            fontSize: 12,
            color: '#616161',
            background: '#eeeeee',
            padding: '2px 8px',
            borderRadius: 12,
          }}
        >
          {workflowLabel}
        </span>
      </div>

      <div style={{ marginTop: 16, display: 'flex', gap: 16, fontSize: 13 }}>
        <div>
          <div style={{ color: '#616161' }}>{WORKSPACE_LABELS.jobs}</div>
          <div style={{ fontWeight: 600 }}>
            {total} | <span style={{ color: '#1976d2' }}>{running}</span> |{' '}
            <span style={{ color: '#2e7d32' }}>{completed}</span> |{' '}
            <span style={{ color: '#d32f2f' }}>{failed}</span>
          </div>
        </div>
        <div>
          <div style={{ color: '#616161' }}>{WORKSPACE_LABELS.executors}</div>
          <div style={{ fontWeight: 600 }}>
            {executorRunning}/{executorAvailable}
          </div>
        </div>
      </div>

      <div style={{ marginTop: 16 }}>
        <Button variant="contained" fullWidth onClick={onClick}>
          {WORKSPACE_LABELS.enter}
        </Button>
      </div>
    </div>
  )
}
