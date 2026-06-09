import styles from './FilterChips.module.css'

export interface FilterChipsProps {
  filters: Array<{ key: string; label: string; count: number }>
  activeKey: string
  onChange: (key: string) => void
}

export function FilterChips({ filters, activeKey, onChange }: FilterChipsProps) {
  return (
    <div className={styles.row} role="list">
      {filters.map((filter) => (
        <button
          key={filter.key}
          type="button"
          role="listitem"
          data-chip={filter.key}
          className={`${styles.chip} ${
            activeKey === filter.key ? styles.active : ''
          }`}
          onClick={() => onChange(filter.key)}
        >
          {filter.label}{' '}
          <span className={styles.count}>（{filter.count}）</span>
        </button>
      ))}
    </div>
  )
}
