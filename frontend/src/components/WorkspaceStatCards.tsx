import styles from './WorkspaceStatCards.module.css'

const ITEMS = [
  { key: 'all', label: '全部', color: 'default' },
  { key: 'pending', label: '等待中', color: 'pending' },
  { key: 'running', label: '运行中', color: 'running' },
  { key: 'completed', label: '已完成', color: 'completed' },
  { key: 'failed', label: '失败', color: 'failed' },
]

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
      {ITEMS.map((item) => (
        <button
          key={item.key}
          type="button"
          data-filter={item.key}
          className={`${styles.pill} ${styles[item.color]} ${
            activeFilter === item.key ? styles.active : ''
          }`}
          onClick={() => onFilterChange(item.key)}
        >
          {item.label}（{counts[item.key] ?? 0}）
        </button>
      ))}
    </div>
  )
}
