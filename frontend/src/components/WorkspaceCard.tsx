import { WORKSPACE_LABELS } from '../labels'

type WorkspaceCardProps = {
  name: string
  pipelineLabel: string
  isSystem?: boolean
  jobStats: Record<string, number>
  agentStatus: { total: number; busy: number; idle: number }
  onClick: () => void
  onDelete?: () => void
}

export default function WorkspaceCard({
  name,
  pipelineLabel,
  isSystem,
  jobStats,
  agentStatus,
  onClick,
  onDelete,
}: WorkspaceCardProps) {
  const total = Object.values(jobStats).reduce((a, b) => a + b, 0)
  const running = jobStats['running'] || 0
  const completed = jobStats['completed'] || 0
  const failed = jobStats['failed'] || 0

  return (
    <div
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
        background: 'var(--md-sys-color-surface-container-low)',
        cursor: 'pointer',
        border: '1px solid var(--md-sys-color-outline-variant)',
        transition: 'box-shadow 0.2s',
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.boxShadow = '0 2px 8px rgba(0,0,0,0.1)'
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.boxShadow = 'none'
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
        }}
      >
        <div>
          <h3 style={{ margin: '0 0 4px', fontSize: 20 }}>{name}</h3>
          <span
            style={{
              fontSize: 12,
              color: 'var(--md-sys-color-on-surface-variant)',
              background: 'var(--md-sys-color-surface-container-highest)',
              padding: '2px 8px',
              borderRadius: 12,
            }}
          >
            {pipelineLabel}
          </span>
        </div>
        {!isSystem && onDelete && (
          <md-icon-button
            onClick={(e: Event) => {
              e.stopPropagation()
              onDelete()
            }}
          >
            <md-icon>delete</md-icon>
          </md-icon-button>
        )}
      </div>

      <div style={{ marginTop: 16, display: 'flex', gap: 16, fontSize: 13 }}>
        <div>
          <div style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>
            {WORKSPACE_LABELS.jobs}
          </div>
          <div style={{ fontWeight: 600 }}>
            {total} |{' '}
            <span style={{ color: 'var(--md-sys-color-primary)' }}>
              {running}
            </span>{' '}
            |{' '}
            <span style={{ color: 'var(--md-sys-color-tertiary)' }}>
              {completed}
            </span>{' '}
            |{' '}
            <span style={{ color: 'var(--md-sys-color-error)' }}>{failed}</span>
          </div>
        </div>
        <div>
          <div style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>
            {WORKSPACE_LABELS.agents}
          </div>
          <div style={{ fontWeight: 600 }}>
            {agentStatus.busy}/{agentStatus.total}
          </div>
        </div>
      </div>

      <div style={{ marginTop: 16 }}>
        <md-filled-button style={{ width: '100%' }} onClick={onClick}>
          {WORKSPACE_LABELS.enter}
        </md-filled-button>
      </div>
    </div>
  )
}
