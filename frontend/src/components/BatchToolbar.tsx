import styles from './BatchToolbar.module.css'

export type BatchFilter = {
  key: string
  label: string
  onClick: () => void
}

export type BatchAction = {
  key: string
  label: string
  variant?: 'text' | 'outlined' | 'filled'
  danger?: boolean
  onClick: () => void
}

export type BatchToolbarProps = {
  selectedCount: number
  filters: BatchFilter[]
  actions: BatchAction[]
  onExitSelectMode: () => void
}

export function BatchToolbar({
  selectedCount,
  filters,
  actions,
  onExitSelectMode,
}: BatchToolbarProps) {
  return (
    <div className={`${styles.batchToolbar} card-elevated`}>
      <span>已选择 {selectedCount} 项</span>
      <div className={styles.batchActions}>
        {filters.map((filter) => (
          <md-text-button key={filter.key} onClick={filter.onClick}>
            {filter.label}
          </md-text-button>
        ))}
        {actions.map((action) => {
          const buttonStyle = action.danger
            ? ({ color: 'var(--md-sys-color-error)' } as React.CSSProperties)
            : undefined
          if (action.variant === 'text') {
            return (
              <md-text-button
                key={action.key}
                onClick={action.onClick}
                style={buttonStyle}
              >
                {action.label}
              </md-text-button>
            )
          }
          if (action.variant === 'filled') {
            return (
              <md-filled-button
                key={action.key}
                onClick={action.onClick}
                style={buttonStyle}
              >
                {action.label}
              </md-filled-button>
            )
          }
          return (
            <md-outlined-button
              key={action.key}
              onClick={action.onClick}
              style={buttonStyle}
            >
              {action.label}
            </md-outlined-button>
          )
        })}
        <md-outlined-button onClick={onExitSelectMode}>退出</md-outlined-button>
      </div>
    </div>
  )
}
