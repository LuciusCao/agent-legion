import { STATUS_FILTER_CONFIG } from '../labels'
import styles from './WorkspaceStatCards.module.css'

const ITEMS = ['all', 'pending', 'running', 'completed', 'failed']

export interface WorkspaceStatCardsProps {
  counts: Record<string, number>
  activeFilter: string
  onFilterChange: (filter: string) => void
}

export function WorkspaceStatCards({
  counts,
  activeFilter,
  onFilterChange,
}: WorkspaceStatCardsProps) {
  return (
    <div className={styles.pills}>
      {ITEMS.map((key) => {
        const config = STATUS_FILTER_CONFIG[key]
        return (
          <button
            key={key}
            type="button"
            data-filter={key}
            className={`${styles.pill} ${
              activeFilter === key ? styles.active : ''
            }`}
            onClick={() => onFilterChange(key)}
          >
            <md-icon>{config.icon}</md-icon>
            <span>
              {config.label}（{counts[key] ?? 0}）
            </span>
          </button>
        )
      })}
    </div>
  )
}
