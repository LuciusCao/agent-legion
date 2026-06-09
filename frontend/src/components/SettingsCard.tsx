import { useState } from 'react'
import styles from './SettingsCard.module.css'

interface SettingsCardProps {
  icon: string
  title: string
  status?: React.ReactNode
  defaultExpanded?: boolean
  children: React.ReactNode
}

export function SettingsCard({
  icon,
  title,
  status,
  defaultExpanded = false,
  children,
}: SettingsCardProps) {
  const [expanded, setExpanded] = useState(defaultExpanded)

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      setExpanded((v) => !v)
    }
  }

  return (
    <div className={`card-outlined ${styles.card}`}>
      <div
        className={styles.header}
        onClick={() => setExpanded((v) => !v)}
        onKeyDown={handleKeyDown}
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        data-testid="settings-card-header"
      >
        <span className={styles.icon}>
          <md-icon>{icon}</md-icon>
        </span>
        <span className={styles.title}>{title}</span>
        {status && <span className={styles.status}>{status}</span>}
        <span className={styles.chevron} aria-hidden="true">
          <md-icon>{expanded ? 'expand_less' : 'expand_more'}</md-icon>
        </span>
      </div>
      {expanded && <div className={styles.body}>{children}</div>}
    </div>
  )
}
