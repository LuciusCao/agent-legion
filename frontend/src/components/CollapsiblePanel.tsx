import { useState } from 'react'
import { MaterialIcon } from './MaterialIcon'
import styles from './CollapsiblePanel.module.css'

export interface CollapsiblePanelProps {
  title: string
  count?: number
  defaultExpanded?: boolean
  children: React.ReactNode
}

export function CollapsiblePanel({
  title,
  count,
  defaultExpanded = false,
  children,
}: CollapsiblePanelProps) {
  const [expanded, setExpanded] = useState(defaultExpanded)

  return (
    <div className={styles.panel} data-testid="collapsible-panel">
      <button
        type="button"
        className={styles.header}
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
      >
        <span className={styles.title}>{title}</span>
        {count !== undefined && (
          <span className={styles.count}>{count} 条</span>
        )}
        <MaterialIcon
          name={expanded ? 'expand_less' : 'expand_more'}
          className={styles.toggleIcon}
          sx={{ fontSize: 20 }}
        />
      </button>
      {expanded && <div className={styles.body}>{children}</div>}
    </div>
  )
}
