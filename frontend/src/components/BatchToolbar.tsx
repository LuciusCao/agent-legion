import { Button } from '@mui/material'
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
          <Button key={filter.key} variant="text" onClick={filter.onClick}>
            {filter.label}
          </Button>
        ))}
        {actions.map((action) => {
          const color = action.danger ? 'error' : undefined
          if (action.variant === 'text') {
            return (
              <Button
                key={action.key}
                variant="text"
                color={color}
                onClick={action.onClick}
              >
                {action.label}
              </Button>
            )
          }
          if (action.variant === 'filled') {
            return (
              <Button
                key={action.key}
                variant="contained"
                color={color}
                onClick={action.onClick}
              >
                {action.label}
              </Button>
            )
          }
          return (
            <Button
              key={action.key}
              variant="outlined"
              color={color}
              onClick={action.onClick}
            >
              {action.label}
            </Button>
          )
        })}
        <Button variant="outlined" onClick={onExitSelectMode}>
          退出
        </Button>
      </div>
    </div>
  )
}
