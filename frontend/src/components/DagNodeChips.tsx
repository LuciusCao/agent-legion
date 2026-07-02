import { useState } from 'react'
import styles from './DagNodeChips.module.css'

const CHIP_LIMIT = 3

export function ChipList({
  title,
  items,
  variant,
}: {
  title: string
  items: string[]
  variant: 'in' | 'out'
}) {
  const [expanded, setExpanded] = useState(false)
  const visible = expanded ? items : items.slice(0, CHIP_LIMIT)
  const hidden = items.length - visible.length

  if (items.length === 0) return null

  return (
    <div className={styles.chipGroup}>
      <div className={styles.chipTitle}>
        {title}（{items.length}）
      </div>
      <div className={styles.chipRow}>
        {visible.map((item) => (
          <span
            key={item}
            className={[
              styles.chip,
              variant === 'out' ? styles.chipOut : '',
            ].join(' ')}
            title={item}
          >
            {item.length > 18 ? item.slice(0, 17) + '…' : item}
          </span>
        ))}
        {hidden > 0 && !expanded && (
          <button
            className={styles.moreButton}
            onClick={(e) => {
              e.stopPropagation()
              setExpanded(true)
            }}
          >
            +{hidden}
          </button>
        )}
      </div>
    </div>
  )
}
